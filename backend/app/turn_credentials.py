import hmac
import hashlib
import time
from typing import Dict, List
from app.config import settings


def generate_turn_credentials(username: str) -> Dict[str, any]:
    """
    Generate dynamic TURN credentials using time-limited credentials method.

    This implements the REST API authentication mechanism for TURN servers:
    https://tools.ietf.org/html/draft-uberti-behave-turn-rest-00

    Args:
        username: Identifier for the user (can be user_id or session_id)

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

    # Generate HMAC-SHA1 credential using shared secret
    turn_password = hmac.new(
        settings.TURN_SECRET.encode('utf-8'),
        turn_username.encode('utf-8'),
        hashlib.sha1
    ).digest().hex()

    # Return ICE server configuration
    return {
        "urls": [
            f"turn:{settings.TURN_HOST}:{settings.TURN_PORT}?transport=udp",
            f"turn:{settings.TURN_HOST}:{settings.TURN_PORT}?transport=tcp",
        ],
        "username": turn_username,
        "credential": turn_password,
        "credentialType": "password"
    }


def get_ice_servers(username: str) -> List[Dict[str, any]]:
    """
    Get complete ICE servers configuration including STUN and TURN.

    Args:
        username: Identifier for the user

    Returns:
        List of ICE server configurations
    """
    ice_servers = [
        # Public STUN servers
        {"urls": "stun:stun.l.google.com:19302"},
        {"urls": "stun:stun1.l.google.com:19302"},
    ]

    # Add TURN server with dynamic credentials
    turn_config = generate_turn_credentials(username)
    ice_servers.append(turn_config)

    return ice_servers
