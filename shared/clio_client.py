"""
Clio API Client Module

A Python client for interacting with the Clio Manage API v4.
Provides methods for fetching matters, creating communications, and managing notes.

Usage:
    from clio_client import ClioClient, Matter
    
    client = ClioClient(api_token="your_token", base_url="https://app.clio.com")
    matters = client.get_matters(status="Open")
"""

import logging
import time
from dataclasses import dataclass
from typing import Optional, Dict, Any, List, Union
from datetime import datetime
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Configure logging
logger = logging.getLogger(__name__)


class ClioAPIError(Exception):
    """
    Custom exception for Clio API errors.
    
    Attributes:
        message (str): Error message
        status_code (int): HTTP status code from the API
        response_data (dict): Raw response data from the API
    """
    
    def __init__(self, message: str, status_code: Optional[int] = None, response_data: Optional[Dict] = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.response_data = response_data or {}
    
    def __str__(self) -> str:
        if self.status_code:
            return f"ClioAPIError [{self.status_code}]: {self.message}"
        return f"ClioAPIError: {self.message}"


@dataclass
class Matter:
    """
    Data class representing a Clio Matter.
    
    Attributes:
        id (int): Unique identifier for the matter
        display_number (str): Human-readable matter number (e.g., "00001-Matter Name")
        name (str): Name/title of the matter
        status (str): Current status (e.g., "Open", "Closed", "Pending")
        client_name (str): Name of the associated client
        matter_type (str): Type/category of the matter
    """
    id: int
    display_number: str
    name: str
    status: str
    client_name: str
    matter_type: str
    
    @classmethod
    def from_api_response(cls, data: Dict[str, Any]) -> "Matter":
        """
        Create a Matter instance from Clio API response data.
        
        Args:
            data: Raw API response data containing 'id', 'attributes', etc.
            
        Returns:
            Matter: Parsed Matter instance
            
        Example:
            >>> data = {
            ...     "id": 12345,
            ...     "attributes": {
            ...         "display_number": "00001-Test Matter",
            ...         "description": "Test Matter",
            ...         "status": "Open"
            ...     },
            ...     "relationships": {...}
            ... }
            >>> matter = Matter.from_api_response(data)
        """
        attributes = data.get("attributes", {})
        relationships = data.get("relationships", {})
        
        # Extract client name from relationships if available
        client_name = ""
        client_data = relationships.get("client", {}).get("data", {})
        if client_data and isinstance(client_data, dict):
            client_name = client_data.get("name", "")
        
        # Extract matter type from relationships
        matter_type = ""
        practice_area = relationships.get("practice_area", {}).get("data", {})
        if practice_area and isinstance(practice_area, dict):
            matter_type = practice_area.get("name", "")
        
        return cls(
            id=data.get("id", 0),
            display_number=attributes.get("display_number", ""),
            name=attributes.get("description", ""),
            status=attributes.get("status", ""),
            client_name=client_name,
            matter_type=matter_type
        )


class ClioClient:
    """
    Client for interacting with the Clio Manage API v4.
    
    This client provides methods to:
    - Fetch matters from Clio
    - Create communications (email logs)
    - Create notes
    
    All methods include automatic retry logic for transient failures.
    
    Attributes:
        api_token (str): Bearer token for authentication
        base_url (str): Base URL for the Clio API (e.g., "https://app.clio.com")
        api_version (str): API version (default: "v4")
        session (requests.Session): Configured HTTP session with retry logic
    
    Example:
        >>> client = ClioClient(
        ...     api_token="your_api_token",
        ...     base_url="https://app.clio.com"
        ... )
        >>> matters = client.get_matters(status="Open", limit=100)
    """
    
    DEFAULT_API_VERSION = "v4"
    DEFAULT_TIMEOUT = 30
    MAX_RETRIES = 3
    BACKOFF_FACTOR = 1.0
    
    def __init__(
        self,
        api_token: str,
        base_url: str,
        api_version: Optional[str] = None
    ):
        """
        Initialize the Clio API client.
        
        Args:
            api_token: Bearer token for API authentication
            base_url: Base URL for Clio API (e.g., "https://app.clio.com")
            api_version: API version to use (default: "v4")
            
        Raises:
            ValueError: If api_token or base_url is empty
        """
        if not api_token:
            raise ValueError("API token is required")
        if not base_url:
            raise ValueError("Base URL is required")
        
        self.api_token = api_token
        self.base_url = base_url.rstrip("/")
        self.api_version = api_version or self.DEFAULT_API_VERSION
        
        # Initialize session with retry configuration
        self.session = requests.Session()
        self._configure_retry()
        
        logger.info(f"ClioClient initialized for {self.base_url} (API {self.api_version})")
    
    def _configure_retry(self) -> None:
        """
        Configure retry logic for HTTP requests.
        
        Retries on:
        - 429 (Too Many Requests)
        - 500, 502, 503, 504 (Server errors)
        
        Uses exponential backoff with the configured backoff factor.
        """
        retry_strategy = Retry(
            total=self.MAX_RETRIES,
            backoff_factor=self.BACKOFF_FACTOR,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"],
            raise_on_status=False
        )
        
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
    
    def _get_headers(self) -> Dict[str, str]:
        """
        Get default headers for API requests.
        
        Returns:
            Dict containing Authorization and Content-Type headers
        """
        return {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
    
    def _request(self, method: str, endpoint: str, **kwargs) -> Dict[str, Any]:
        url = f"{self.base_url}{endpoint}"
        headers = self._get_headers()
        if "headers" in kwargs:
            headers.update(kwargs.pop("headers"))

        logger.debug(f"Making {method} request to {url}")

        try:
            response = self.session.request(
                method=method,
                url=url,
                headers=headers,
                timeout=self.DEFAULT_TIMEOUT,
                **kwargs
            )
            logger.debug(f"Response status: {response.status_code}")

            # Try to parse JSON; fallback to raw text
            try:
                parsed_response = response.json()
            except ValueError:
                parsed_response = response.text

            # If response is not successful
            if not response.ok:
                # Handle dict or string safely
                if isinstance(parsed_response, dict):
                    error_field = parsed_response.get("error")
                    if isinstance(error_field, dict):
                        error_message = error_field.get("message", response.text)
                    else:
                        error_message = str(error_field) if error_field else response.text
                    response_data = parsed_response
                else:
                    error_message = str(parsed_response) or response.text
                    response_data = {}

                logger.error(f"Clio API error: {response.status_code} - {error_message}")
                raise ClioAPIError(
                    message=error_message,
                    status_code=response.status_code,
                    response_data=response_data
                )

            # Success: return dict if possible, else empty dict
            if isinstance(parsed_response, dict):
                return parsed_response
            else:
                return {}

        except requests.exceptions.Timeout:
            logger.error(f"Request timeout for {url}")
            raise ClioAPIError(f"Request timeout after {self.DEFAULT_TIMEOUT}s")
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Connection error for {url}: {e}")
            raise ClioAPIError(f"Connection error: {str(e)}")
        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed for {url}: {e}")
            raise ClioAPIError(f"Request failed: {str(e)}")
    
    def _parse_nested_response(
        self,
        response: Dict[str, Any]
    ) -> Union[List[Dict], Dict, None]:
        """
        Parse Clio's nested JSON response structure.
        
        Clio API returns data in the format:
        {
            "data": [...] or {...},
            "meta": {...}
        }
        
        Args:
            response: Raw API response dictionary
            
        Returns:
            The 'data' portion of the response (list or dict)
        """
        if not response:
            return None
        return response.get("data")
    
    def get_matters(
        self,
        status: Optional[str] = None,
        limit: int = 1000
    ) -> List[Matter]:
        """
        Fetch matters from the Clio API.
        
        Args:
            status: Filter by matter status (e.g., "Open", "Closed", "Pending")
            limit: Maximum number of matters to retrieve (default: 1000)
            
        Returns:
            List of Matter objects
            
        Raises:
            ClioAPIError: On API errors
            
        Example:
            >>> matters = client.get_matters(status="Open", limit=100)
            >>> for matter in matters:
            ...     print(f"{matter.display_number}: {matter.name}")
        """
        endpoint = f"/api/{self.api_version}/matters.json"
        
        params = {"limit": limit}
        if status:
            params["status"] = status
        
        logger.info(f"Fetching matters with params: {params}")
        
        try:
            response = self._request("GET", endpoint, params=params)
            data = self._parse_nested_response(response)
            
            if not data:
                logger.info("No matters found")
                return []
            
            matters = [Matter.from_api_response(item) for item in data]
            logger.info(f"Successfully fetched {len(matters)} matters")
            
            return matters
            
        except ClioAPIError:
            raise
        except Exception as e:
            logger.error(f"Unexpected error fetching matters: {e}")
            raise ClioAPIError(f"Failed to fetch matters: {str(e)}")
    
    def create_communication(
        self,
        matter_id: int,
        subject: str,
        body: str,
        sender_email: str,
        received_at: Optional[Union[str, datetime]] = None
    ) -> Dict[str, Any]:
        """
        Create a communication (email log) in Clio.
        
        Posts to /api/v4/communications.json
        
        Args:
            matter_id: ID of the matter to associate with the communication
            subject: Subject line of the communication
            body: Body content of the communication
            sender_email: Email address of the sender
            received_at: Timestamp when communication was received 
                        (ISO 8601 string or datetime object)
            
        Returns:
            API response data containing the created communication
            
        Raises:
            ClioAPIError: On API errors
            ValueError: On invalid input parameters
            
        Example:
            >>> result = client.create_communication(
            ...     matter_id=12345,
            ...     subject="Meeting Notes",
            ...     body="Here are the notes from our meeting...",
            ...     sender_email="client@example.com",
            ...     received_at="2024-01-15T10:30:00Z"
            ... )
        """
        if not matter_id:
            raise ValueError("matter_id is required")
        if not subject:
            raise ValueError("subject is required")
        if not body:
            raise ValueError("body is required")
        if not sender_email:
            raise ValueError("sender_email is required")
        
        endpoint = f"/api/{self.api_version}/communications.json"
        
        # Format received_at if datetime object
        if isinstance(received_at, datetime):
            received_at = received_at.isoformat()
        elif received_at is None:
            received_at = datetime.utcnow().isoformat() + "Z"
        
        payload = {
            "data": {
                "type": "Communication",
                "attributes": {
                    "subject": subject,
                    "body": body,
                    "sender_email_address": sender_email,
                    "received_at": received_at
                },
                "relationships": {
                    "matter": {
                        "data": {
                            "type": "Matter",
                            "id": matter_id
                        }
                    }
                }
            }
        }
        
        logger.info(f"Creating communication for matter {matter_id}: {subject}")
        
        try:
            response = self._request("POST", endpoint, json=payload)
            logger.info(f"Successfully created communication")
            return self._parse_nested_response(response) or {}
            
        except ClioAPIError:
            raise
        except Exception as e:
            logger.error(f"Unexpected error creating communication: {e}")
            raise ClioAPIError(f"Failed to create communication: {str(e)}")
    
    def create_note(
        self,
        matter_id: int,
        subject: str,
        details: str
    ) -> Dict[str, Any]:
        """
        Create a note in Clio.
        
        Posts to /api/v4/notes.json
        
        Args:
            matter_id: ID of the matter to associate with the note
            subject: Subject/title of the note
            details: Detailed content of the note
            
        Returns:
            API response data containing the created note
            
        Raises:
            ClioAPIError: On API errors
            ValueError: On invalid input parameters
            
        Example:
            >>> result = client.create_note(
            ...     matter_id=12345,
            ...     subject="Case Update",
            ...     details="Important developments in the case..."
            ... )
        """
        if not matter_id:
            raise ValueError("matter_id is required")
        if not subject:
            raise ValueError("subject is required")
        if not details:
            raise ValueError("details is required")
        
        endpoint = f"/api/{self.api_version}/notes.json"
        
        payload = {
            "data": {
                "type": "Note",
                "attributes": {
                    "subject": subject,
                    "details": details
                },
                "relationships": {
                    "matter": {
                        "data": {
                            "type": "Matter",
                            "id": matter_id
                        }
                    }
                }
            }
        }
        
        logger.info(f"Creating note for matter {matter_id}: {subject}")
        
        try:
            response = self._request("POST", endpoint, json=payload)
            logger.info(f"Successfully created note")
            return self._parse_nested_response(response) or {}
            
        except ClioAPIError:
            raise
        except Exception as e:
            logger.error(f"Unexpected error creating note: {e}")
            raise ClioAPIError(f"Failed to create note: {str(e)}")
    
    def get_matter_by_id(self, matter_id: int) -> Optional[Matter]:
        """
        Fetch a single matter by its ID.
        
        Args:
            matter_id: The ID of the matter to fetch
            
        Returns:
            Matter object if found, None otherwise
            
        Raises:
            ClioAPIError: On API errors
        """
        endpoint = f"/api/{self.api_version}/matters/{matter_id}.json"
        
        logger.info(f"Fetching matter {matter_id}")
        
        try:
            response = self._request("GET", endpoint)
            data = self._parse_nested_response(response)
            
            if not data:
                return None
            
            return Matter.from_api_response(data)
            
        except ClioAPIError as e:
            if e.status_code == 404:
                logger.info(f"Matter {matter_id} not found")
                return None
            raise
        except Exception as e:
            logger.error(f"Unexpected error fetching matter: {e}")
            raise ClioAPIError(f"Failed to fetch matter: {str(e)}")


# Convenience function for quick client creation
def create_clio_client(
    api_token: str,
    base_url: str = "https://app.clio.com",
    api_version: Optional[str] = None
) -> ClioClient:
    """
    Factory function to create a ClioClient instance.
    
    Args:
        api_token: Bearer token for authentication
        base_url: Base URL for Clio API
        api_version: API version to use
        
    Returns:
        Configured ClioClient instance
        
    Example:
        >>> client = create_clio_client("your_token")
        >>> matters = client.get_matters()
    """
    return ClioClient(api_token=api_token, base_url=base_url, api_version=api_version)
