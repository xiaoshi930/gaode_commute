"""Sensor platform for Gaode Commute Tracker integration."""
from datetime import datetime
import logging

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_DRIVING_DISTANCE,
    ATTR_DRIVING_DURATION,
    ATTR_TRANSIT_DISTANCE,
    ATTR_TRANSIT_DURATION,
    COORDINATOR,
    DEFAULT_NAME,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the Gaode Commute Tracker sensor platform."""
    coordinator = hass.data[DOMAIN][entry.entry_id][COORDINATOR]
    
    # 检查配置是否有效
    if not coordinator.data:
        _LOGGER.error("无法初始化传感器：协调器数据为空")
        return
        
    async_add_entities([GaodeCommuteSensor(coordinator, entry)])


class GaodeCommuteSensor(CoordinatorEntity, SensorEntity):
    """Implementation of a Gaode Commute Tracker sensor."""

    def __init__(self, coordinator, entry):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._entry = entry
        
        # 使用自定义名称
        custom_name = entry.data.get("custom_name", "通勤")
        self._attr_name = f"{custom_name}通勤"
        
        # 使用拼音作为实体ID
        from pypinyin import lazy_pinyin
        pinyin = ''.join(lazy_pinyin(custom_name))
        self.entity_id = f"sensor.gaode_{pinyin}"
        
        # 设置设备信息
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": f"高德通勤 - {custom_name}",
            "manufacturer": "高德",
        }
        
        self._attr_unique_id = f"{entry.entry_id}"
        self._attr_available = False  # 初始状态设为不可用

    @property
    def native_value(self):
        """Return the state of the sensor."""
        if not self.coordinator.data:
            self._attr_available = False
            return None
            
        # 返回驾车通勤时间（分钟）
        driving_data = self.coordinator.data.get("driving", {})
        driving_duration_seconds = driving_data.get("duration", 0)
        
        # 如果数据无效，标记为不可用
        if driving_duration_seconds <= 0:
            self._attr_available = False
            return None
            
        self._attr_available = True
        return round(driving_duration_seconds / 60)

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        if not self.coordinator.data:
            return {}
            
        driving_data = self.coordinator.data.get("driving", {})
        transit_data = self.coordinator.data.get("transit", {})
        
        # 驾车通勤时间（秒转分钟或小时）
        driving_duration_seconds = driving_data.get("duration", 0)
        driving_duration_minutes = driving_duration_seconds / 60
        
        # 公交通勤时间（秒转分钟或小时）
        transit_duration_seconds = transit_data.get("duration", 0)
        transit_duration_minutes = transit_duration_seconds / 60
        
        # 驾车通勤距离（米转公里）
        driving_distance_meters = driving_data.get("distance", 0)
        driving_distance_km = driving_distance_meters / 1000
        
        # 公交通勤距离（米转公里）
        transit_distance_meters = transit_data.get("distance", 0)
        transit_distance_km = transit_distance_meters / 1000
        
        # 格式化显示
        if driving_duration_seconds <= 0:
            driving_duration_display = "未知"
        elif driving_duration_minutes > 120:  # 大于2小时
            driving_duration_display = f"{driving_duration_minutes / 60:.2f}小时"
        else:
            driving_duration_display = f"{int(driving_duration_minutes)}分钟"
            
        if transit_duration_seconds <= 0:
            transit_duration_display = "未知"
        elif transit_duration_minutes > 120:  # 大于2小时
            transit_duration_display = f"{transit_duration_minutes / 60:.2f}小时"
        else:
            transit_duration_display = f"{int(transit_duration_minutes)}分钟"
        
        # 处理无效距离
        driving_distance_display = "未知" if driving_distance_meters <= 0 else f"{driving_distance_km:.1f}公里"
        transit_distance_display = "未知" if transit_distance_meters <= 0 else f"{transit_distance_km:.1f}公里"
        
        return {
            ATTR_DRIVING_DURATION: driving_duration_display,
            ATTR_DRIVING_DISTANCE: driving_distance_display,
            ATTR_TRANSIT_DURATION: transit_duration_display,
            ATTR_TRANSIT_DISTANCE: transit_distance_display,
        }

    @property
    def icon(self):
        """Return the icon of the sensor."""
        if not self._attr_available:
            return "mdi:car-off"
        return "mdi:car-clock"
