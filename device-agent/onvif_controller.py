#!/usr/bin/env python3
"""
ONVIF PTZ Controller for MuMu Camera

Handles PTZ (Pan-Tilt-Zoom) and Focus control for ONVIF-compatible cameras.
"""

import logging
import asyncio
from typing import Optional, Dict, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ONVIFConfig:
    """ONVIF camera configuration"""
    ip: str
    port: int = 80
    username: str = "admin"
    password: str = ""
    wsdl_dir: str = "/opt/mumucam/onvif/wsdl"


class ONVIFController:
    """
    ONVIF PTZ Controller

    Provides async-safe PTZ control for ONVIF cameras.
    Uses synchronous onvif-zeep library wrapped in executor.
    """

    def __init__(self, config: ONVIFConfig):
        self.config = config
        self._camera = None
        self._media = None
        self._ptz = None
        self._imaging = None
        self._profile_token = None
        self._video_source_token = None
        self._initialized = False
        self._lock = asyncio.Lock()

        logger.info(f"[onvif] Controller created for {config.ip}:{config.port}")

    async def initialize(self) -> bool:
        """Initialize ONVIF connection (async wrapper)"""
        async with self._lock:
            if self._initialized:
                return True

            try:
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, self._init_sync)
                return result
            except Exception as e:
                logger.error(f"[onvif] Failed to initialize: {e}")
                return False

    def _init_sync(self) -> bool:
        """Synchronous initialization (runs in executor)"""
        try:
            from onvif import ONVIFCamera

            logger.info(f"[onvif] Connecting to {self.config.ip}:{self.config.port}...")

            self._camera = ONVIFCamera(
                self.config.ip,
                self.config.port,
                self.config.username,
                self.config.password,
                wsdl_dir=self.config.wsdl_dir
            )

            # Create services
            self._media = self._camera.create_media_service()

            # Get profiles
            profiles = self._media.GetProfiles()
            if not profiles:
                logger.error("[onvif] No profiles found")
                return False

            self._profile_token = profiles[0].token
            logger.info(f"[onvif] Using profile: {profiles[0].Name} (token={self._profile_token})")

            # Try to create PTZ service
            try:
                self._ptz = self._camera.create_ptz_service()
                logger.info("[onvif] PTZ service available")
            except Exception as e:
                logger.warning(f"[onvif] PTZ service not available: {e}")
                self._ptz = None

            # Try to create Imaging service (for focus control)
            try:
                self._imaging = self._camera.create_imaging_service()
                # Get video source token for imaging
                video_sources = self._media.GetVideoSources()
                if video_sources:
                    self._video_source_token = video_sources[0].token
                    logger.info(f"[onvif] Imaging service available (source={self._video_source_token})")
            except Exception as e:
                logger.warning(f"[onvif] Imaging service not available: {e}")
                self._imaging = None

            self._initialized = True
            logger.info("[onvif] Initialization complete")
            return True

        except ImportError:
            logger.error("[onvif] onvif-zeep not installed. Run: pip install onvif-zeep")
            return False
        except Exception as e:
            logger.error(f"[onvif] Initialization error: {e}", exc_info=True)
            return False

    async def move(self, pan: float = 0, tilt: float = 0, zoom: float = 0, duration: float = 0.5) -> Dict[str, Any]:
        """
        Move camera with continuous movement

        Args:
            pan: Pan speed (-1.0 to 1.0, negative=left, positive=right)
            tilt: Tilt speed (-1.0 to 1.0, negative=down, positive=up)
            zoom: Zoom speed (-1.0 to 1.0, negative=wide, positive=tele)
            duration: How long to move in seconds

        Returns:
            Dict with success status and message
        """
        if not await self.initialize():
            return {"success": False, "error": "ONVIF not initialized"}

        if not self._ptz:
            return {"success": False, "error": "PTZ not available"}

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                self._move_sync,
                pan, tilt, zoom, duration
            )
            return result
        except Exception as e:
            logger.error(f"[onvif] Move error: {e}")
            return {"success": False, "error": str(e)}

    def _move_sync(self, pan: float, tilt: float, zoom: float, duration: float) -> Dict[str, Any]:
        """Synchronous move operation"""
        import time

        try:
            # Clamp values
            pan = max(-1.0, min(1.0, pan))
            tilt = max(-1.0, min(1.0, tilt))
            zoom = max(-1.0, min(1.0, zoom))

            # Create continuous move request
            req = self._ptz.create_type("ContinuousMove")
            req.ProfileToken = self._profile_token
            req.Velocity = {
                "PanTilt": {"x": pan, "y": tilt},
                "Zoom": {"x": zoom}
            }

            logger.info(f"[onvif] Moving: pan={pan}, tilt={tilt}, zoom={zoom}, duration={duration}s")

            # Start movement
            self._ptz.ContinuousMove(req)

            # Wait for duration
            time.sleep(duration)

            # Stop movement
            self._ptz.Stop({
                "ProfileToken": self._profile_token,
                "PanTilt": True,
                "Zoom": True
            })

            logger.info("[onvif] Move complete")
            return {"success": True, "message": "Move complete"}

        except Exception as e:
            logger.error(f"[onvif] Move sync error: {e}")
            return {"success": False, "error": str(e)}

    async def continuous_start(self, pan: float = 0, tilt: float = 0, zoom: float = 0) -> Dict[str, Any]:
        """
        Start continuous movement (non-blocking, use stop() to stop)

        Args:
            pan: Pan speed (-1.0 to 1.0)
            tilt: Tilt speed (-1.0 to 1.0)
            zoom: Zoom speed (-1.0 to 1.0)

        Returns:
            Dict with success status
        """
        if not await self.initialize():
            return {"success": False, "error": "ONVIF not initialized"}

        if not self._ptz:
            return {"success": False, "error": "PTZ not available"}

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                self._continuous_start_sync,
                pan, tilt, zoom
            )
            return result
        except Exception as e:
            logger.error(f"[onvif] Continuous start error: {e}")
            return {"success": False, "error": str(e)}

    def _continuous_start_sync(self, pan: float, tilt: float, zoom: float) -> Dict[str, Any]:
        """Synchronous continuous start (non-blocking)"""
        try:
            pan = max(-1.0, min(1.0, pan))
            tilt = max(-1.0, min(1.0, tilt))
            zoom = max(-1.0, min(1.0, zoom))

            req = self._ptz.create_type("ContinuousMove")
            req.ProfileToken = self._profile_token
            req.Velocity = {
                "PanTilt": {"x": pan, "y": tilt},
                "Zoom": {"x": zoom}
            }

            logger.info(f"[onvif] Starting continuous: pan={pan}, tilt={tilt}, zoom={zoom}")
            self._ptz.ContinuousMove(req)

            return {"success": True, "message": "Continuous move started"}
        except Exception as e:
            logger.error(f"[onvif] Continuous start sync error: {e}")
            return {"success": False, "error": str(e)}

    async def stop(self) -> Dict[str, Any]:
        """Stop all PTZ movement"""
        if not await self.initialize():
            return {"success": False, "error": "ONVIF not initialized"}

        if not self._ptz:
            return {"success": False, "error": "PTZ not available"}

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, self._stop_sync)
            return result
        except Exception as e:
            logger.error(f"[onvif] Stop error: {e}")
            return {"success": False, "error": str(e)}

    def _stop_sync(self) -> Dict[str, Any]:
        """Synchronous stop operation"""
        try:
            self._ptz.Stop({
                "ProfileToken": self._profile_token,
                "PanTilt": True,
                "Zoom": True
            })
            logger.info("[onvif] PTZ stopped")
            return {"success": True, "message": "Stopped"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def focus(self, direction: str, speed: float = 0.5, duration: float = 0.3) -> Dict[str, Any]:
        """
        Control focus

        Args:
            direction: "near" or "far"
            speed: Focus speed (0.0 to 1.0)
            duration: How long to adjust in seconds

        Returns:
            Dict with success status and message
        """
        if not await self.initialize():
            return {"success": False, "error": "ONVIF not initialized"}

        if not self._imaging or not self._video_source_token:
            return {"success": False, "error": "Imaging service not available"}

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                self._focus_sync,
                direction, speed, duration
            )
            return result
        except Exception as e:
            logger.error(f"[onvif] Focus error: {e}")
            return {"success": False, "error": str(e)}

    def _focus_sync(self, direction: str, speed: float, duration: float) -> Dict[str, Any]:
        """Synchronous focus operation"""
        import time

        try:
            speed = max(0.0, min(1.0, speed))

            # Direction: near = negative, far = positive
            focus_speed = speed if direction == "far" else -speed

            logger.info(f"[onvif] Focus {direction} at speed {speed} for {duration}s")

            # Continuous focus move
            self._imaging.Move({
                "VideoSourceToken": self._video_source_token,
                "Focus": {
                    "Continuous": {
                        "Speed": focus_speed
                    }
                }
            })

            time.sleep(duration)

            # Stop focus
            self._imaging.Stop({"VideoSourceToken": self._video_source_token})

            logger.info("[onvif] Focus complete")
            return {"success": True, "message": f"Focus {direction} complete"}

        except Exception as e:
            logger.error(f"[onvif] Focus sync error: {e}")
            return {"success": False, "error": str(e)}

    async def auto_focus(self) -> Dict[str, Any]:
        """Trigger auto focus (one-shot)"""
        if not await self.initialize():
            return {"success": False, "error": "ONVIF not initialized"}

        if not self._imaging or not self._video_source_token:
            return {"success": False, "error": "Imaging service not available"}

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, self._auto_focus_sync)
            return result
        except Exception as e:
            logger.error(f"[onvif] Auto focus error: {e}")
            return {"success": False, "error": str(e)}

    def _auto_focus_sync(self) -> Dict[str, Any]:
        """Synchronous auto focus operation"""
        try:
            # Get current imaging settings
            settings = self._imaging.GetImagingSettings({
                "VideoSourceToken": self._video_source_token
            })

            # Set to auto focus mode
            self._imaging.SetImagingSettings({
                "VideoSourceToken": self._video_source_token,
                "ImagingSettings": {
                    "Focus": {
                        "AutoFocusMode": "AUTO"
                    }
                }
            })

            logger.info("[onvif] Auto focus triggered")
            return {"success": True, "message": "Auto focus triggered"}

        except Exception as e:
            logger.error(f"[onvif] Auto focus sync error: {e}")
            return {"success": False, "error": str(e)}

    async def go_to_preset(self, preset: int) -> Dict[str, Any]:
        """Go to a saved preset position"""
        if not await self.initialize():
            return {"success": False, "error": "ONVIF not initialized"}

        if not self._ptz:
            return {"success": False, "error": "PTZ not available"}

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, self._goto_preset_sync, preset)
            return result
        except Exception as e:
            logger.error(f"[onvif] Preset error: {e}")
            return {"success": False, "error": str(e)}

    def _goto_preset_sync(self, preset: int) -> Dict[str, Any]:
        """Synchronous go to preset operation"""
        try:
            # Get presets
            presets = self._ptz.GetPresets({"ProfileToken": self._profile_token})

            if preset < 1 or preset > len(presets):
                return {"success": False, "error": f"Invalid preset {preset}, available: 1-{len(presets)}"}

            preset_token = presets[preset - 1].token

            self._ptz.GotoPreset({
                "ProfileToken": self._profile_token,
                "PresetToken": preset_token
            })

            logger.info(f"[onvif] Moving to preset {preset}")
            return {"success": True, "message": f"Moving to preset {preset}"}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def get_capabilities(self) -> Dict[str, Any]:
        """Get camera PTZ capabilities"""
        if not await self.initialize():
            return {"success": False, "error": "ONVIF not initialized"}

        caps = {
            "ptz": self._ptz is not None,
            "imaging": self._imaging is not None,
            "focus": self._imaging is not None and self._video_source_token is not None
        }

        return {"success": True, "capabilities": caps}

    async def reboot(self) -> Dict[str, Any]:
        """
        Reboot the camera via ONVIF SystemReboot.
        Resets _initialized to clear stale state after reboot.
        """
        if not await self.initialize():
            return {"success": False, "error": "ONVIF not initialized"}

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, self._reboot_sync)
            if result.get("success"):
                async with self._lock:
                    self._initialized = False
                    self._camera = None
                    self._media = None
                    self._ptz = None
                    self._imaging = None
            return result
        except Exception as e:
            logger.error(f"[onvif] Reboot error: {e}")
            return {"success": False, "error": str(e)}

    def _reboot_sync(self) -> Dict[str, Any]:
        """Synchronous reboot operation"""
        try:
            logger.info(f"[onvif] Sending SystemReboot to {self.config.ip}...")
            self._camera.devicemgmt.SystemReboot()
            logger.info(f"[onvif] SystemReboot sent to {self.config.ip}")
            return {"success": True, "message": f"Reboot command sent to {self.config.ip}"}
        except Exception as e:
            logger.error(f"[onvif] SystemReboot error: {e}")
            return {"success": False, "error": str(e)}

    async def is_reachable(self) -> bool:
        """
        Lightweight probe to check if camera is reachable.
        Uses GetSystemDateAndTime which doesn't require auth on most cameras.
        """
        try:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self._is_reachable_sync)
        except Exception:
            return False

    def _is_reachable_sync(self) -> bool:
        """Synchronous reachability check"""
        try:
            from onvif import ONVIFCamera
            cam = ONVIFCamera(
                self.config.ip,
                self.config.port,
                self.config.username,
                self.config.password,
                wsdl_dir=self.config.wsdl_dir
            )
            cam.devicemgmt.GetSystemDateAndTime()
            return True
        except Exception:
            return False

    async def sync_time(self) -> Dict[str, Any]:
        """
        Sync camera time with local system (UTC).
        Uses ONVIF SetSystemDateAndTime to push current time to camera.
        """
        if not await self.initialize():
            return {"success": False, "error": "ONVIF not initialized"}

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, self._sync_time_sync)
            return result
        except Exception as e:
            logger.error(f"[onvif] Time sync error: {e}")
            return {"success": False, "error": str(e)}

    def _sync_time_sync(self) -> Dict[str, Any]:
        """Synchronous time sync operation"""
        from datetime import datetime, timezone

        try:
            now = datetime.now(timezone.utc)

            self._camera.devicemgmt.SetSystemDateAndTime({
                "DateTimeType": "Manual",
                "DaylightSavings": False,
                "UTCDateTime": {
                    "Date": {
                        "Year": now.year,
                        "Month": now.month,
                        "Day": now.day
                    },
                    "Time": {
                        "Hour": now.hour,
                        "Minute": now.minute,
                        "Second": now.second
                    }
                }
            })

            logger.info(f"[onvif] Time synced to {now.strftime('%Y-%m-%d %H:%M:%S')} UTC → {self.config.ip}")
            return {"success": True, "message": f"Time synced to {now.isoformat()}"}

        except Exception as e:
            logger.error(f"[onvif] Time sync error for {self.config.ip}: {e}")
            return {"success": False, "error": str(e)}
