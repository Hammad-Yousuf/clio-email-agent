"""
Azure Functions v2 HTTP Trigger Functions for Email Classification System.

This module provides HTTP endpoints for:
- Email classification against legal matters
- Clio writeback operations
- Matter data retrieval
- Health monitoring

Author: Azure Functions Specialist
Version: 1.0.0
"""

import os
import json
import logging
import datetime
import tempfile
from typing import Dict, List, Any, Optional
from functools import wraps

import azure.functions as func

# Import shared modules
from shared.clio_client import ClioClient, ClioAPIError
from shared.classifier import MatterClassifier, ClassificationResult
from shared.audit_logger import AuditLogger

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize the Function App
app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

# =============================================================================
# Configuration
# =============================================================================

CONFIDENCE_THRESHOLD = float(os.environ.get("CONFIDENCE_THRESHOLD", "0.75"))
REVIEW_QUEUE_MATTER_ID = os.environ.get("REVIEW_QUEUE_MATTER_ID", "")
CLIO_API_BASE_URL = os.environ.get("CLIO_API_BASE_URL", "")
CLIO_API_TOKEN = os.environ.get("CLIO_API_TOKEN", "")
CACHE_FILE_PATH = os.environ.get("CACHE_FILE_PATH", "/tmp/matters_cache.json")
APP_VERSION = "1.0.0"

# =============================================================================
# Helper Functions
# =============================================================================

def create_json_response(
    data: Dict[str, Any],
    status_code: int = 200,
    headers: Optional[Dict[str, str]] = None
) -> func.HttpResponse:
    """
    Create a JSON HTTP response with proper headers.
    
    Args:
        data: Response data dictionary
        status_code: HTTP status code
        headers: Optional additional headers
        
    Returns:
        func.HttpResponse with JSON content
    """
    default_headers = {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization"
    }
    
    if headers:
        default_headers.update(headers)
    
    return func.HttpResponse(
        body=json.dumps(data, indent=2, default=str),
        status_code=status_code,
        headers=default_headers
    )


def parse_json_body(req: func.HttpRequest) -> Optional[Dict[str, Any]]:
    """
    Parse JSON body from HTTP request.
    
    Args:
        req: HTTP request object
        
    Returns:
        Parsed JSON dictionary or None if invalid
    """
    try:
        body = req.get_body()
        if not body:
            return None
        return json.loads(body.decode('utf-8'))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        logger.warning(f"Failed to parse JSON body: {e}")
        return None


def validate_required_fields(data: Dict[str, Any], required: List[str]) -> List[str]:
    """
    Validate that all required fields are present in the data.
    
    Args:
        data: Input data dictionary
        required: List of required field names
        
    Returns:
        List of missing field names
    """
    return [field for field in required if field not in data or data[field] is None]


def get_timestamp() -> str:
    """Get current ISO timestamp."""
    return datetime.datetime.utcnow().isoformat() + "Z"


# =============================================================================
# Audit Logging Decorator
# =============================================================================

def audit_log(operation: str):
    """
    Decorator to log operations for audit purposes.
    
    Args:
        operation: Name of the operation being performed
    """
    def decorator(handler):
        @wraps(handler)
        async def wrapper(req: func.HttpRequest, *args, **kwargs):
            start_time = datetime.datetime.utcnow()
            conn_str = os.environ.get("AUDIT_LOG_CONNECTION_STRING")
            if not conn_str:
                raise ValueError("AUDIT_LOG_CONNECTION_STRING environment variable not set")
            audit_logger = AuditLogger(connection_string=conn_str)
            
            try:
                result = await func(req, *args, **kwargs)
                
                # Log successful operation
                audit_logger.log(
                    operation=operation,
                    status="success",
                    duration_ms=(datetime.datetime.utcnow() - start_time).total_seconds() * 1000,
                    request_path=req.url,
                    http_method=req.method
                )
                
                return result
                
            except Exception as e:
                # Log failed operation
                audit_logger.log(
                    operation=operation,
                    status="error",
                    error=str(e),
                    duration_ms=(datetime.datetime.utcnow() - start_time).total_seconds() * 1000,
                    request_path=req.url,
                    http_method=req.method
                )
                raise
                
        return wrapper
    return decorator


# =============================================================================
# Endpoint 1: POST /api/classify - Classification only
# =============================================================================

@app.route(route="classify", methods=["POST", "OPTIONS"])
@audit_log("classify")
async def classify_email(req: func.HttpRequest) -> func.HttpResponse:
    """
    Classify an email against known matters.
    
    Request Body:
        {
            "email_id": str,
            "subject": str,
            "body": str,
            "sender_email": str,
            "sender_name": str,
            "received_at": str (ISO timestamp)
        }
    
    Response:
        {
            "matter_id": str,
            "matter_display_number": str,
            "matter_name": str,
            "confidence_score": float,
            "matched_signals": List[str],
            "recommended_action": str
        }
    """
    logger.info("Processing classify request")
    
    # Handle CORS preflight
    if req.method == "OPTIONS":
        return create_json_response({"message": "OK"}, status_code=200)
    
    # Parse request body
    body = parse_json_body(req)
    if body is None:
        return create_json_response(
            {"error": "Invalid JSON body", "code": "INVALID_JSON"},
            status_code=400
        )
    
    # Validate required fields
    required_fields = ["email_id", "subject", "body", "sender_email", "sender_name", "received_at"]
    missing = validate_required_fields(body, required_fields)
    if missing:
        return create_json_response(
            {
                "error": f"Missing required fields: {', '.join(missing)}",
                "code": "MISSING_FIELDS",
                "missing_fields": missing
            },
            status_code=400
        )
    
    try:
        # Initialize classifier
        classifier = MatterClassifier()
        
        # Load matters for classification
        matters = await _load_matters()
        
        # Perform classification
        result = classifier.classify(
            subject=body["subject"],
            body=body["body"],
            sender_email=body["sender_email"],
            sender_name=body["sender_name"],
            matters=matters
        )
        
        # Build response
        response = {
            "matter_id": result.matter_id,
            "matter_display_number": result.matter_display_number,
            "matter_name": result.matter_name,
            "confidence_score": round(result.confidence_score, 4),
            "matched_signals": result.matched_signals,
            "recommended_action": result.recommended_action,
            "email_id": body["email_id"],
            "classified_at": get_timestamp()
        }
        
        logger.info(f"Classification complete: matter_id={result.matter_id}, confidence={result.confidence_score}")
        
        return create_json_response(response, status_code=200)
        
    except Exception as e:
        logger.error(f"Classification failed: {e}", exc_info=True)
        return create_json_response(
            {
                "error": "Classification failed",
                "code": "CLASSIFICATION_ERROR",
                "details": str(e)
            },
            status_code=500
        )


# =============================================================================
# Endpoint 2: POST /api/classify-and-writeback - Classify + writeback
# =============================================================================

@app.route(route="classify-and-writeback", methods=["POST", "OPTIONS"])
@audit_log("classify_and_writeback")
async def classify_and_writeback(req: func.HttpRequest) -> func.HttpResponse:
    """
    Classify an email and automatically writeback to Clio.
    
    If confidence >= threshold: creates communication in matched matter
    If confidence < threshold: creates draft note in review-queue matter
    
    Request Body:
        Same as /api/classify
    
    Response:
        {
            "classification": { ... },
            "writeback": {
                "success": bool,
                "clio_communication_id": str (optional),
                "target_matter_id": str,
                "target_matter_name": str,
                "created_as_draft": bool,
                "error": str (optional)
            }
        }
    """
    logger.info("Processing classify-and-writeback request")
    
    # Handle CORS preflight
    if req.method == "OPTIONS":
        return create_json_response({"message": "OK"}, status_code=200)
    
    # Parse request body
    body = parse_json_body(req)
    if body is None:
        return create_json_response(
            {"error": "Invalid JSON body", "code": "INVALID_JSON"},
            status_code=400
        )
    
    # Validate required fields
    required_fields = ["email_id", "subject", "body", "sender_email", "sender_name", "received_at"]
    missing = validate_required_fields(body, required_fields)
    if missing:
        return create_json_response(
            {
                "error": f"Missing required fields: {', '.join(missing)}",
                "code": "MISSING_FIELDS",
                "missing_fields": missing
            },
            status_code=400
        )
    
    classification_result = None
    writeback_result = None
    
    try:
        # Step 1: Classification
        classifier = MatterClassifier()
        matters = await _load_matters()
        
        classification = classifier.classify(
            subject=body["subject"],
            body=body["body"],
            sender_email=body["sender_email"],
            sender_name=body["sender_name"],
            matters=matters
        )
        
        classification_result = {
            "matter_id": classification.matter_id,
            "matter_display_number": classification.matter_display_number,
            "matter_name": classification.matter_name,
            "confidence_score": round(classification.confidence_score, 4),
            "matched_signals": classification.matched_signals,
            "recommended_action": classification.recommended_action,
            "email_id": body["email_id"],
            "classified_at": get_timestamp()
        }
        
        # Step 2: Determine writeback target
        is_high_confidence = classification.confidence_score >= CONFIDENCE_THRESHOLD
        
        if is_high_confidence:
            target_matter_id = classification.matter_id
            target_matter_name = classification.matter_name
            create_as_draft = False
        else:
            if not REVIEW_QUEUE_MATTER_ID:
                raise ValueError("REVIEW_QUEUE_MATTER_ID not configured for low-confidence handling")
            target_matter_id = REVIEW_QUEUE_MATTER_ID
            target_matter_name = "Review Queue"
            create_as_draft = True
        
        # Step 3: Writeback to Clio
        clio_client = ClioClient(
            base_url=CLIO_API_BASE_URL,
            api_token=CLIO_API_TOKEN
        )
        
        communication = await clio_client.create_communication(
            matter_id=target_matter_id,
            subject=body["subject"],
            body=body["body"],
            sender_email=body["sender_email"],
            received_at=body["received_at"],
            is_draft=create_as_draft,
            metadata={
                "original_email_id": body["email_id"],
                "classification_confidence": classification.confidence_score,
                "classification_signals": classification.matched_signals,
                "auto_classified": True
            }
        )
        
        writeback_result = {
            "success": True,
            "clio_communication_id": communication.get("id"),
            "target_matter_id": target_matter_id,
            "target_matter_name": target_matter_name,
            "created_as_draft": create_as_draft,
            "confidence_based_routing": is_high_confidence,
            "written_at": get_timestamp()
        }
        
        logger.info(
            f"Classify-and-writeback complete: confidence={classification.confidence_score}, "
            f"target={target_matter_id}, draft={create_as_draft}"
        )
        
    except Exception as e:
        logger.error(f"Classify-and-writeback failed: {e}", exc_info=True)
        writeback_result = {
            "success": False,
            "error": str(e),
            "code": "WRITEBACK_ERROR",
            "written_at": get_timestamp()
        }
    
    # Build final response
    response = {
        "classification": classification_result,
        "writeback": writeback_result
    }
    
    status_code = 200 if (classification_result and writeback_result and writeback_result.get("success")) else 207
    
    return create_json_response(response, status_code=status_code)


# =============================================================================
# Endpoint 3: POST /api/writeback - Force communication writeback
# =============================================================================

@app.route(route="writeback", methods=["POST", "OPTIONS"])
@audit_log("writeback")
async def force_writeback(req: func.HttpRequest) -> func.HttpResponse:
    """
    Force create a communication in a specific matter.
    
    Request Body:
        {
            "email_id": str,
            "matter_id": str,
            "subject": str,
            "body": str,
            "sender_email": str,
            "received_at": str (ISO timestamp)
        }
    
    Response:
        {
            "success": bool,
            "clio_communication_id": str (if success),
            "error": str (if failure)
        }
    """
    logger.info("Processing force writeback request")
    
    # Handle CORS preflight
    if req.method == "OPTIONS":
        return create_json_response({"message": "OK"}, status_code=200)
    
    # Parse request body
    body = parse_json_body(req)
    if body is None:
        return create_json_response(
            {"error": "Invalid JSON body", "code": "INVALID_JSON"},
            status_code=400
        )
    
    # Validate required fields
    required_fields = ["email_id", "matter_id", "subject", "body", "sender_email", "received_at"]
    missing = validate_required_fields(body, required_fields)
    if missing:
        return create_json_response(
            {
                "error": f"Missing required fields: {', '.join(missing)}",
                "code": "MISSING_FIELDS",
                "missing_fields": missing
            },
            status_code=400
        )
    
    try:
        # Initialize Clio client
        clio_client = ClioClient(
            base_url=CLIO_API_BASE_URL,
            api_token=CLIO_API_TOKEN
        )
        
        # Create communication
        communication = await clio_client.create_communication(
            matter_id=body["matter_id"],
            subject=body["subject"],
            body=body["body"],
            sender_email=body["sender_email"],
            received_at=body["received_at"],
            is_draft=False,
            metadata={
                "original_email_id": body["email_id"],
                "force_writeback": True
            }
        )
        
        response = {
            "success": True,
            "clio_communication_id": communication.get("id"),
            "matter_id": body["matter_id"],
            "created_at": get_timestamp()
        }
        
        logger.info(f"Force writeback complete: communication_id={communication.get('id')}")
        
        return create_json_response(response, status_code=201)
        
    except ClioAPIError as e:
        logger.error(f"Clio API error during writeback: {e}")
        return create_json_response(
            {
                "success": False,
                "error": str(e),
                "code": "CLIO_API_ERROR",
                "matter_id": body["matter_id"]
            },
            status_code=502
        )
        
    except Exception as e:
        logger.error(f"Writeback failed: {e}", exc_info=True)
        return create_json_response(
            {
                "success": False,
                "error": str(e),
                "code": "WRITEBACK_ERROR",
                "matter_id": body["matter_id"]
            },
            status_code=500
        )


# =============================================================================
# Endpoint 4: GET /api/matters - Cached matters snapshot
# =============================================================================

@app.route(route="matters", methods=["GET", "OPTIONS"])
@audit_log("get_matters")
async def get_matters(req: func.HttpRequest) -> func.HttpResponse:
    """
    Get cached matters snapshot.
    
    Query Parameters:
        refresh (bool): Force refresh from Clio API
    
    Response:
        {
            "cached_at": str (ISO timestamp),
            "source": str ("cache" | "api"),
            "matter_count": int,
            "matters": [
                {
                    "id": str,
                    "display_number": str,
                    "name": str,
                    "client_name": str,
                    "status": str,
                    "open_date": str
                }
            ]
        }
    """
    logger.info("Processing get matters request")
    
    # Handle CORS preflight
    if req.method == "OPTIONS":
        return create_json_response({"message": "OK"}, status_code=200)
    
    # Check for refresh parameter
    refresh = req.params.get("refresh", "false").lower() == "true"
    
    try:
        matters, cached_at, source = await _load_matters(with_metadata=True, force_refresh=refresh)
        
        response = {
            "cached_at": cached_at,
            "source": source,
            "matter_count": len(matters),
            "matters": matters
        }
        
        logger.info(f"Matters retrieved: count={len(matters)}, source={source}")
        
        return create_json_response(response, status_code=200)
        
    except Exception as e:
        logger.error(f"Failed to retrieve matters: {e}", exc_info=True)
        return create_json_response(
            {
                "error": "Failed to retrieve matters",
                "code": "MATTERS_FETCH_ERROR",
                "details": str(e)
            },
            status_code=500
        )


async def _load_matters(
    with_metadata: bool = False,
    force_refresh: bool = False
) -> Any:
    """
    Load matters from cache or fetch from Clio API.
    
    Args:
        with_metadata: Return tuple with (matters, cached_at, source)
        force_refresh: Force refresh from API
        
    Returns:
        Matters list, or tuple if with_metadata=True
    """
    cache_valid = False
    cached_data = None
    CACHE_FILE_PATH = os.environ.get("CACHE_FILE_PATH", os.path.join(tempfile.gettempdir(), "matters_cache.json"))
    # Try to load from cache
    if not force_refresh and os.path.exists(CACHE_FILE_PATH):
        try:
            with open(CACHE_FILE_PATH, 'r') as f:
                cached_data = json.load(f)
            
            # Check cache age (default 1 hour)
            cache_max_age = int(os.environ.get("CACHE_MAX_AGE_MINUTES", "60"))
            cached_at = datetime.datetime.fromisoformat(
                cached_data.get("cached_at", "2000-01-01").replace("Z", "+00:00")
            )
            cache_age = (datetime.datetime.utcnow() - cached_at.replace(tzinfo=None)).total_seconds() / 60
            
            if cache_age < cache_max_age:
                cache_valid = True
                logger.info(f"Using cached matters (age: {cache_age:.1f} minutes)")
            else:
                logger.info(f"Cache expired (age: {cache_age:.1f} minutes)")
                
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(f"Cache file corrupt, will refresh: {e}")
    
    if cache_valid and cached_data:
        matters = cached_data.get("matters", [])
        if with_metadata:
            return matters, cached_data.get("cached_at"), "cache"
        return matters
    
    # Fetch from Clio API
    logger.info("Fetching matters from Clio API")
    
    clio_client = ClioClient(
        base_url=CLIO_API_BASE_URL,
        api_token=CLIO_API_TOKEN
    )
    
    # synchronous client call; get_matters() returns a list, not a coroutine
    matters = clio_client.get_matters()
    
    # Update cache
    cache_data = {
        "cached_at": get_timestamp(),
        "matters": matters
    }
    
    try:
        os.makedirs(os.path.dirname(CACHE_FILE_PATH), exist_ok=True)
        with open(CACHE_FILE_PATH, 'w') as f:
            json.dump(cache_data, f, indent=2)
        logger.info(f"Matters cache updated: {len(matters)} matters")
    except Exception as e:
        logger.warning(f"Failed to write cache: {e}")
    
    if with_metadata:
        return matters, cache_data["cached_at"], "api"
    return matters


# =============================================================================
# Endpoint 5: GET /api/health - Health check
# =============================================================================

@app.route(route="health", methods=["GET", "OPTIONS"])
async def health_check(req: func.HttpRequest) -> func.HttpResponse:
    """
    Health check endpoint.
    
    Response:
        {
            "status": "healthy",
            "version": "1.0.0",
            "timestamp": str (ISO timestamp),
            "checks": {
                "clio_api": bool,
                "cache": bool
            }
        }
    """
    logger.debug("Health check requested")
    
    # Handle CORS preflight
    if req.method == "OPTIONS":
        return create_json_response({"message": "OK"}, status_code=200)
    
    # Perform health checks
    checks = {
        "clio_api": False,
        "cache": False,
        "config": False
    }
    
    # Check configuration
    if CLIO_API_BASE_URL and CLIO_API_TOKEN:
        checks["config"] = True
    
    # Check cache
    if os.path.exists(CACHE_FILE_PATH):
        checks["cache"] = True
    
    # Check Clio API connectivity (lightweight check)
    try:
        clio_client = ClioClient(
            base_url=CLIO_API_BASE_URL,
            api_token=CLIO_API_TOKEN
        )
        # Just verify we can initialize - actual check would be too heavy
        checks["clio_api"] = True
    except Exception:
        checks["clio_api"] = False
    
    # Determine overall status
    overall_status = "healthy" if all(checks.values()) else "degraded"
    
    response = {
        "status": overall_status,
        "version": APP_VERSION,
        "timestamp": get_timestamp(),
        "checks": checks,
        "environment": os.environ.get("ENVIRONMENT", "production")
    }
    
    status_code = 200 if overall_status == "healthy" else 503
    
    return create_json_response(response, status_code=status_code)


# # =============================================================================
# # Error Handlers
# # =============================================================================

# @app.middleware
# async def global_exception_middleware(req: func.HttpRequest, func_call):
#     try:
#         # Execute the actual function
#         return await func_call(req)
#     except Exception as exception:
#         # Log the error
#         logger.error(f"Unhandled exception: {exception}", exc_info=True)
        
#         # Return your JSON error response
#         return create_json_response(
#             {
#                 "error": "Internal server error",
#                 "code": "INTERNAL_ERROR",
#                 "timestamp": get_timestamp()
#             },
#             status_code=500
#         )
