"""
API rate limit module
Based on slowapi, prevents interfaces from being maliciously flooded.
Applies to all API routes by default (adjustable via api.rate_limit config),
static files and health check paths are exempt.
"""
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from config import config

# Read per-minute request limit from config
_rate_limit_per_minute = config.get('api.rate_limit', 60)

# Global rate limiter instance: keyed by client IP, default limit {rate_limit}/minute
# Routes can override default via @limiter.limit("30/minute")
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[f"{_rate_limit_per_minute}/minute"],
    headers_enabled=True,  # Return X-RateLimit-* response headers
)

# Path prefixes exempt from rate limiting (static resources, health check)
_EXEMPT_PREFIXES = ('/static', '/api/health', '/docs', '/openapi.json', '/redoc')


def is_exempt(path: str) -> bool:
    """Check if request path is exempt from rate limiting"""
    return any(path.startswith(prefix) for prefix in _EXEMPT_PREFIXES)


def rate_limit_exceeded_handler(request, exc: RateLimitExceeded):
    """Custom response handler for rate limit exceeded"""
    from service.i18n import get_lang_from_request, _
    from service.logger import get_logger

    logger = get_logger('rate_limiter')
    lang = get_lang_from_request(request)
    client_ip = get_remote_address(request)
    logger.warning(f"Rate limit exceeded: IP={client_ip}, Path={request.url.path}, Limit={_rate_limit_per_minute}/minute")

    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=429,
        content={
            'success': False,
            'message': _('api.rate_limit_exceeded', lang, _rate_limit_per_minute),
            'detail': f"Request rate exceeded: {_rate_limit_per_minute} requests per minute"
        },
        media_type="application/json; charset=utf-8"
    )
