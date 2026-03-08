"""
Azure Table Storage Audit Logger Module

This module provides audit logging functionality for Clio email classification events,
storing structured audit records in Azure Table Storage for compliance and monitoring.

Usage:
    from audit_logger import AuditLogger, AuditEvent
    
    logger = AuditLogger(connection_string="your-connection-string")
    logger.log_classification(
        email_id="msg_123",
        email_subject="Contract Review",
        sender="client@example.com",
        classification_result={"matter_id": "mat_456", "confidence": 0.95},
        action_taken="classified_and_written"
    )
"""

import json
import logging
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from azure.data.tables import TableServiceClient, TableClient
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError, AzureError

# Configure module-level logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


@dataclass
class AuditEvent:
    """
    Dataclass representing an audit event for storage in Azure Table Storage.
    
    Attributes:
        PartitionKey: Date-based partition key (e.g., "2026-03-07")
        RowKey: Unique identifier for the row (UUID-based)
        Timestamp: ISO format timestamp of the event
        EventType: Type of event ("classification" or "writeback")
        EmailId: Unique identifier for the email
        EmailSubject: Subject line of the email
        Sender: Email address of the sender
        MatterId: Associated matter ID (if applicable)
        ConfidenceScore: Classification confidence score (0.0 - 1.0)
        ActionTaken: Action performed (e.g., "classified", "written_back")
        ErrorMessage: Error details if an error occurred
        ClioResponse: Response from Clio API (if applicable)
    """
    PartitionKey: str
    RowKey: str
    Timestamp: str
    EventType: str
    EmailId: str
    EmailSubject: str = ""
    Sender: str = ""
    MatterId: str = ""
    ConfidenceScore: float = 0.0
    ActionTaken: str = ""
    ErrorMessage: str = ""
    ClioResponse: str = ""
    
    def to_entity(self) -> Dict[str, Any]:
        """
        Convert the dataclass to a dictionary suitable for Azure Table Storage.
        
        Returns:
            Dictionary representation of the audit event
        """
        return {
            "PartitionKey": self.PartitionKey,
            "RowKey": self.RowKey,
            "Timestamp": self.Timestamp,
            "EventType": self.EventType,
            "EmailId": self.EmailId,
            "EmailSubject": self.EmailSubject,
            "Sender": self.Sender,
            "MatterId": self.MatterId,
            "ConfidenceScore": self.ConfidenceScore,
            "ActionTaken": self.ActionTaken,
            "ErrorMessage": self.ErrorMessage,
            "ClioResponse": self.ClioResponse
        }
    
    @classmethod
    def from_entity(cls, entity: Dict[str, Any]) -> "AuditEvent":
        """
        Create an AuditEvent from an Azure Table Storage entity.
        
        Args:
            entity: Dictionary from Table Storage
            
        Returns:
            AuditEvent instance
        """
        return cls(
            PartitionKey=entity.get("PartitionKey", ""),
            RowKey=entity.get("RowKey", ""),
            Timestamp=entity.get("Timestamp", ""),
            EventType=entity.get("EventType", ""),
            EmailId=entity.get("EmailId", ""),
            EmailSubject=entity.get("EmailSubject", ""),
            Sender=entity.get("Sender", ""),
            MatterId=entity.get("MatterId", ""),
            ConfidenceScore=entity.get("ConfidenceScore", 0.0),
            ActionTaken=entity.get("ActionTaken", ""),
            ErrorMessage=entity.get("ErrorMessage", ""),
            ClioResponse=entity.get("ClioResponse", "")
        )


class AuditLogger:
    """
    Audit logger for Clio email classification events using Azure Table Storage.
    
    This class provides methods to log classification and writeback events
    to Azure Table Storage with proper error handling and non-blocking operation.
    
    Attributes:
        connection_string: Azure Storage connection string
        table_name: Name of the table for audit logs
        table_client: Azure Table Client instance
    """
    
    def __init__(self, connection_string: str, table_name: str = "ClioAuditLogs"):
        """
        Initialize the AuditLogger with Azure Storage connection.
        
        Args:
            connection_string: Azure Storage connection string
            table_name: Name of the table for audit logs (default: "ClioAuditLogs")
        """
        self.connection_string = connection_string
        self.table_name = table_name
        self.table_client: Optional[TableClient] = None
        self._initialized = False
        
        try:
            self._initialize_client()
        except Exception as e:
            logger.error(f"Failed to initialize AuditLogger: {str(e)}")
            # Don't raise - logging should be non-blocking

    def log(self, operation: str, **kwargs):
        # Example: just log to console for local dev
        print(f"[AuditLog] {operation}: {kwargs}")
        # In production, write to Azure Table or storage
    
    def _initialize_client(self) -> None:
        """
        Initialize the Azure Table Storage client.
        
        This method creates the table client and ensures the table exists.
        """
        try:
            self.table_service_client = TableServiceClient.from_connection_string(
                conn_str=self.connection_string
            )
            self.table_client = self.table_service_client.get_table_client(
                table_name=self.table_name
            )
            self.ensure_table_exists()
            self._initialized = True
            logger.info(f"AuditLogger initialized with table: {self.table_name}")
        except Exception as e:
            logger.error(f"Error initializing table client: {str(e)}")
            raise
    
    def ensure_table_exists(self) -> bool:
        """
        Create the audit log table if it doesn't exist.
        
        Returns:
            True if table exists or was created successfully, False otherwise
        """
        if not self.table_client:
            logger.warning("Table client not initialized, cannot ensure table exists")
            return False
        
        try:
            self.table_client.create_table()
            logger.info(f"Created audit table: {self.table_name}")
            return True
        except ResourceExistsError:
            logger.debug(f"Audit table already exists: {self.table_name}")
            return True
        except AzureError as e:
            logger.error(f"Azure error ensuring table exists: {str(e)}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error ensuring table exists: {str(e)}")
            return False
    
    def _generate_partition_key(self) -> str:
        """
        Generate a date-based partition key for the current time.
        
        Returns:
            Date string in format "YYYY-MM-DD"
        """
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    def _generate_row_key(self) -> str:
        """
        Generate a unique row key using UUID and timestamp.
        
        Returns:
            Unique row key string
        """
        timestamp = datetime.now(timezone.utc).strftime("%H%M%S%f")
        unique_id = str(uuid.uuid4())[:8]
        return f"{timestamp}-{unique_id}"
    
    def _get_current_timestamp(self) -> str:
        """
        Get the current timestamp in ISO format.
        
        Returns:
            ISO format timestamp string
        """
        return datetime.now(timezone.utc).isoformat()
    
    def _safe_serialize(self, obj: Any) -> str:
        """
        Safely serialize an object to JSON string.
        
        Args:
            obj: Object to serialize
            
        Returns:
            JSON string or empty string if serialization fails
        """
        try:
            if isinstance(obj, str):
                return obj
            return json.dumps(obj, default=str)
        except Exception as e:
            logger.warning(f"Failed to serialize object: {str(e)}")
            return str(obj)
    
    def _log_event(self, event: AuditEvent) -> bool:
        """
        Internal method to log an audit event to Azure Table Storage.
        
        Args:
            event: AuditEvent to log
            
        Returns:
            True if logged successfully, False otherwise
        """
        if not self._initialized or not self.table_client:
            # Log to console as fallback
            logger.warning(f"Audit logger not initialized. Event would be logged: {asdict(event)}")
            return False
        
        try:
            entity = event.to_entity()
            self.table_client.create_entity(entity=entity)
            logger.debug(f"Logged audit event: {event.EventType} for email {event.EmailId}")
            return True
        except AzureError as e:
            logger.error(f"Azure error logging audit event: {str(e)}")
            # Log to console as fallback
            logger.info(f"FALLBACK AUDIT LOG: {asdict(event)}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error logging audit event: {str(e)}")
            # Log to console as fallback
            logger.info(f"FALLBACK AUDIT LOG: {asdict(event)}")
            return False
    
    def log_classification(
        self,
        email_id: str,
        email_subject: str,
        sender: str,
        classification_result: Dict[str, Any],
        action_taken: str,
        matter_id: Optional[str] = None,
        error: Optional[str] = None
    ) -> bool:
        """
        Log a classification event to the audit table.
        
        Args:
            email_id: Unique identifier for the email
            email_subject: Subject line of the email
            sender: Email address of the sender
            classification_result: Dictionary containing classification results
                Expected keys: matter_id, confidence, etc.
            action_taken: Action performed (e.g., "classified", "written_back")
            matter_id: Optional matter ID (overrides classification_result)
            error: Optional error message if classification failed
            
        Returns:
            True if logged successfully, False otherwise
        """
        try:
            # Extract values from classification_result
            result_matter_id = classification_result.get("matter_id", "") if classification_result else ""
            confidence = classification_result.get("confidence", 0.0) if classification_result else 0.0
            
            # Use provided matter_id if available
            final_matter_id = matter_id if matter_id else result_matter_id
            
            event = AuditEvent(
                PartitionKey=self._generate_partition_key(),
                RowKey=self._generate_row_key(),
                Timestamp=self._get_current_timestamp(),
                EventType="classification",
                EmailId=email_id,
                EmailSubject=email_subject or "",
                Sender=sender or "",
                MatterId=final_matter_id or "",
                ConfidenceScore=float(confidence) if confidence else 0.0,
                ActionTaken=action_taken or "",
                ErrorMessage=error or "",
                ClioResponse=""
            )
            
            return self._log_event(event)
            
        except Exception as e:
            logger.error(f"Error creating classification audit event: {str(e)}")
            # Log to console as fallback
            logger.info(f"FALLBACK AUDIT LOG - Classification: email_id={email_id}, error={error}")
            return False
    
    def log_writeback(
        self,
        email_id: str,
        matter_id: str,
        writeback_type: str,
        clio_response: Any,
        error: Optional[str] = None
    ) -> bool:
        """
        Log a writeback event to the audit table.
        
        Args:
            email_id: Unique identifier for the email
            matter_id: Matter ID associated with the writeback
            writeback_type: Type of writeback (e.g., "document", "time_entry", "note")
            clio_response: Response from Clio API (will be serialized to JSON)
            error: Optional error message if writeback failed
            
        Returns:
            True if logged successfully, False otherwise
        """
        try:
            # Serialize Clio response
            serialized_response = self._safe_serialize(clio_response)
            
            event = AuditEvent(
                PartitionKey=self._generate_partition_key(),
                RowKey=self._generate_row_key(),
                Timestamp=self._get_current_timestamp(),
                EventType="writeback",
                EmailId=email_id,
                EmailSubject="",
                Sender="",
                MatterId=matter_id or "",
                ConfidenceScore=0.0,
                ActionTaken=writeback_type or "",
                ErrorMessage=error or "",
                ClioResponse=serialized_response
            )
            
            return self._log_event(event)
            
        except Exception as e:
            logger.error(f"Error creating writeback audit event: {str(e)}")
            # Log to console as fallback
            logger.info(f"FALLBACK AUDIT LOG - Writeback: email_id={email_id}, matter_id={matter_id}, error={error}")
            return False
    
    def query_events(
        self,
        event_type: Optional[str] = None,
        email_id: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 100
    ) -> list:
        """
        Query audit events from the table.
        
        Args:
            event_type: Filter by event type ("classification" or "writeback")
            email_id: Filter by email ID
            start_date: Start date for query (YYYY-MM-DD format)
            end_date: End date for query (YYYY-MM-DD format)
            limit: Maximum number of results to return
            
        Returns:
            List of AuditEvent objects
        """
        if not self._initialized or not self.table_client:
            logger.warning("Audit logger not initialized, cannot query events")
            return []
        
        try:
            # Build filter query
            filters = []
            
            if event_type:
                filters.append(f"EventType eq '{event_type}'")
            
            if email_id:
                filters.append(f"EmailId eq '{email_id}'")
            
            # Date range filter on PartitionKey
            if start_date and end_date:
                filters.append(f"PartitionKey ge '{start_date}' and PartitionKey le '{end_date}'")
            elif start_date:
                filters.append(f"PartitionKey ge '{start_date}'")
            elif end_date:
                filters.append(f"PartitionKey le '{end_date}'")
            
            filter_query = " and ".join(filters) if filters else None
            
            # Query entities
            entities = self.table_client.query_entities(
                query_filter=filter_query,
                results_per_page=limit
            )
            
            events = [AuditEvent.from_entity(entity) for entity in entities]
            logger.debug(f"Queried {len(events)} audit events")
            return events
            
        except AzureError as e:
            logger.error(f"Azure error querying audit events: {str(e)}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error querying audit events: {str(e)}")
            return []


# Convenience function for quick logging
def create_audit_logger(connection_string: str, table_name: str = "ClioAuditLogs") -> AuditLogger:
    """
    Factory function to create an AuditLogger instance.
    
    Args:
        connection_string: Azure Storage connection string
        table_name: Name of the table for audit logs
        
    Returns:
        AuditLogger instance
    """
    return AuditLogger(connection_string=connection_string, table_name=table_name)
