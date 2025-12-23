import hmac
import hashlib
import time
import base64
from typing import Dict, List
from app.config import settings


def generate_turn_credentials(username: str, use_public_host: bool = False) -> Dict[str, any]:
    """
    Generate dynamic TURN credentials using time-limited credentials method.

    This implements the REST API authentication mechanism for TURN servers:
    https://tools.ietf.org/html/draft-uberti-behave-turn-rest-00

    Args:
        username: Identifier for the user (can be user_id or session_id)
        use_public_host: If True, use TURN_PUBLIC_HOST (for browsers).
                        If False, use TURN_HOST (for Docker devices).

    Returns:
        Dictionary with TURN credentials including:
        - urls: List of TURN server URLs
        - username: Time-limited username
        - credential: HMAC-generated password
        - credentialType: Always "password"
    """
    # Create time-limited username (timestamp:username)
    timestamp = int(time.time()) + settings.TURN_TTL
    turn_username = f"{timestamp}:{username}"

    # Generate HMAC-SHA1 credential using shared secret (base64 encoded)
    turn_password = base64.b64encode(
        hmac.new(
            settings.TURN_SECRET.encode('utf-8'),
            turn_username.encode('utf-8'),
            hashlib.sha1
        ).digest()
    ).decode('utf-8')

    # Choose hostname based on client type
    turn_host = settings.TURN_PUBLIC_HOST if use_public_host else settings.TURN_HOST

    # Return ICE server configuration
    return {
        "urls": [
            f"turn:{turn_host}:{settings.TURN_PORT}?transport=udp",
            f"turn:{turn_host}:{settings.TURN_PORT}?transport=tcp",
        ],
        "username": turn_username,
        "credential": turn_password,
        "credentialType": "password"
    }


def get_ice_servers(username: str, use_public_host: bool = False) -> List[Dict[str, any]]:
    """
    Get complete ICE servers configuration including STUN and TURN.

    Args:
        username: Identifier for the user
        use_public_host: If True, use public TURN host for browsers

    Returns:
        List of ICE server configurations
    """
    ice_servers = [
        # Public STUN servers
        {"urls": "stun:stun.l.google.com:19302"},
        {"urls": "stun:stun1.l.google.com:19302"},
    ]

    # Add TURN server with dynamic credentials
    turn_config = generate_turn_credentials(username, use_public_host=use_public_host)
    ice_servers.append(turn_config)

    return ice_servers
