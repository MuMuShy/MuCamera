"""
LiveKit SFU integration service.

Provides token generation and ingress management for LiveKit mode.
Only active when LIVEKIT_ENABLED=true.
"""

import logging
from typing import Optional
from livekit.api import LiveKitAPI, AccessToken, VideoGrants
from livekit.api.ingress_service import CreateIngressRequest
from livekit.protocol.ingress import IngressInput
from app.config import settings

logger = logging.getLogger("mumucam")

# Module-level API client (lazy-initialized)
_lk_api: Optional[LiveKitAPI] = None


def _get_api() -> LiveKitAPI:
    global _lk_api
    if _lk_api is None:
        _lk_api = LiveKitAPI(
            url=settings.LIVEKIT_URL.replace("ws://", "http://").replace("wss://", "https://"),
            api_key=settings.LIVEKIT_API_KEY,
            api_secret=settings.LIVEKIT_API_SECRET,
        )
    return _lk_api


def get_room_name(device_id: str) -> str:
    """Generate a deterministic room name for a device."""
    return f"device-{device_id}"


def generate_viewer_token(device_id: str, user_id: str) -> str:
    """Generate a subscribe-only token for a viewer to watch a device's LiveKit room."""
    room_name = get_room_name(device_id)
    token = (
        AccessToken(settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET)
        .with_identity(f"viewer-{user_id}")
        .with_name(f"Viewer {user_id}")
        .with_grants(VideoGrants(
            room_join=True,
            room=room_name,
            can_subscribe=True,
            can_publish=False,
        ))
        .with_ttl(86400)
    )
    return token.to_jwt()


async def create_ingress(device_id: str) -> dict:
    """
    Create an RTMP ingress entry for a device.
    Returns dict with ingress_id and rtmp_url (the stream key is embedded in the URL).
    """
    api = _get_api()
    room_name = get_room_name(device_id)

    try:
        ingress = await api.ingress.create_ingress(
            CreateIngressRequest(
                input_type=IngressInput.RTMP_INPUT,
                name=f"device-{device_id}",
                room_name=room_name,
                participant_identity=f"device-{device_id}",
                participant_name=f"Camera {device_id}",
            )
        )

        rtmp_url = ingress.url
        stream_key = ingress.stream_key

        logger.info(f"[livekit] Created ingress for {device_id}: id={ingress.ingress_id}")
        return {
            "ingress_id": ingress.ingress_id,
            "rtmp_url": rtmp_url,
            "stream_key": stream_key,
        }
    except Exception as e:
        logger.error(f"[livekit] Failed to create ingress for {device_id}: {e}")
        raise


async def delete_ingress(ingress_id: str):
    """Delete an ingress entry (called when device goes offline)."""
    api = _get_api()
    try:
        from livekit.api.ingress_service import DeleteIngressRequest
        await api.ingress.delete_ingress(
            DeleteIngressRequest(ingress_id=ingress_id)
        )
        logger.info(f"[livekit] Deleted ingress {ingress_id}")
    except Exception as e:
        logger.error(f"[livekit] Failed to delete ingress {ingress_id}: {e}")


async def list_ingresses(room_name: Optional[str] = None) -> list:
    """List active ingresses, optionally filtered by room."""
    api = _get_api()
    try:
        from livekit.api.ingress_service import ListIngressRequest
        resp = await api.ingress.list_ingress(
            ListIngressRequest(room_name=room_name or "")
        )
        return resp.items or []
    except Exception as e:
        logger.error(f"[livekit] Failed to list ingresses: {e}")
        return []
