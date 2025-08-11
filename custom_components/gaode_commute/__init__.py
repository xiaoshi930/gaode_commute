"""The Gaode Commute Tracker integration."""
import asyncio
from datetime import timedelta
import logging

import aiohttp
import async_timeout
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_NAME,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

from .const import (
    CONF_API_KEY,
    CONF_CITY,
    CONF_UPDATE_INTERVAL,
    CONF_ORIGIN,
    CONF_DESTINATION,
    COORDINATOR,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Gaode Commute Tracker from a config entry."""
    # 验证配置
    required_keys = [CONF_API_KEY, CONF_CITY, CONF_ORIGIN, CONF_DESTINATION]
    for key in required_keys:
        if key not in entry.data:
            _LOGGER.error(f"缺少必要的配置项：{key}")
            return False
            
    api_key = entry.data[CONF_API_KEY]
    city = entry.data[CONF_CITY]
    update_interval = entry.data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
    
    # 获取起点和终点（支持直接输入或从实体属性获取）
    def _get_coordinates(value):
        if isinstance(value, str) and "," in value:  # 直接输入格式
            return value
        elif isinstance(value, str):  # 实体模式
            # 处理可能包含 "entity:" 前缀的实体ID
            entity_id = value
            if value.startswith("entity:"):
                entity_id = value[7:]  # 移除 "entity:" 前缀
            
            entity = hass.states.get(entity_id)
            if entity is None:
                _LOGGER.error(f"实体 {value} 不存在，尝试查找的实体ID: {entity_id}")
                return None
            
            attrs = entity.attributes
            if "longitude" not in attrs or "latitude" not in attrs:
                _LOGGER.error(f"实体 {entity_id} 缺少经纬度属性")
                return None
            
            return f"{attrs['longitude']},{attrs['latitude']}"
        return None

    origin = _get_coordinates(entry.data[CONF_ORIGIN])
    destination = _get_coordinates(entry.data[CONF_DESTINATION])
    if origin is None or destination is None:
        return False
    
    # 检查API Key格式
    if not isinstance(api_key, str) or len(api_key) != 32:
        _LOGGER.error("API Key格式无效")
        return False
        
    # 检查城市格式
    if not isinstance(city, str) or not city:
        _LOGGER.error("城市名称无效")
        return False
        
    # 检查更新间隔（支持浮点数转换）
    try:
        update_interval = int(float(update_interval))  # 兼容浮点数输入
        if update_interval < 1 or update_interval > 60:
            raise ValueError
    except (ValueError, TypeError):
        _LOGGER.error(f"更新间隔无效：{update_interval}（必须为1-60的整数）")
        return False
        
    # 检查起点和终点格式
    if not isinstance(origin, str) or not origin:
        _LOGGER.error("起点格式无效")
        return False
        
    if not isinstance(destination, str) or not destination:
        _LOGGER.error("终点格式无效")
        return False

    session = async_get_clientsession(hass)

    coordinator = GaodeDataUpdateCoordinator(
        hass,
        _LOGGER,
        api_key=api_key,
        city=city,
        origin=origin,
        destination=destination,
        update_interval=timedelta(minutes=update_interval),
        session=session,
    )

    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception as e:
        _LOGGER.error(f"初始化协调器失败：{e}")
        return False

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        COORDINATOR: coordinator,
    }

    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception as e:
        _LOGGER.error(f"设置平台失败：{e}")
        return False

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    try:
        unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
        if unload_ok:
            hass.data[DOMAIN].pop(entry.entry_id, None)
            _LOGGER.debug(f"成功卸载配置项：{entry.entry_id}")
        else:
            _LOGGER.error(f"卸载平台失败：{entry.entry_id}")
        return unload_ok
    except Exception as e:
        _LOGGER.error(f"卸载配置项时出错：{e}")
        return False


class GaodeDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the API."""

    def __init__(
        self,
        hass: HomeAssistant,
        logger,
        api_key,
        city,
        origin,
        destination,
        update_interval,
        session,
    ):
        """Initialize."""
        self.hass = hass
        self.api_key = api_key
        self.city = city
        self.origin = origin
        self.destination = destination
        self.session = session
        
        # 初始化日志记录器
        self.logger = logger
        
        # 记录初始化信息（隐藏敏感信息）
        logger.debug(f"初始化高德通勤追踪器：城市={city}, 起点类型={'实体' if isinstance(origin, str) and origin.startswith('entity:') else '坐标'}, 终点类型={'实体' if isinstance(destination, str) and destination.startswith('entity:') else '坐标'}")
        logger.debug(f"更新间隔：{update_interval}分钟")
        
        # 检查是否使用实体作为位置
        if isinstance(origin, str) and origin.startswith("entity:"):
            self.origin_entity_id = origin.split(":", 1)[1]
            logger.debug(f"使用实体作为起点：{self.origin_entity_id}")
        
        if isinstance(destination, str) and destination.startswith("entity:"):
            self.destination_entity_id = destination.split(":", 1)[1]
            logger.debug(f"使用实体作为终点：{self.destination_entity_id}")

        super().__init__(hass, logger, name=DOMAIN, update_interval=update_interval)

    async def _async_update_data(self):
        """Update data via API."""
        try:
            async with async_timeout.timeout(10):
                # 记录更新开始
                self.logger.debug("开始更新高德通勤数据")
                
                # 更新实体位置（如果使用的是实体）
                if not await self._update_entity_locations():
                    self.logger.error("更新实体位置失败")
                    return {
                        "driving": {"duration": 0, "distance": 0},
                        "transit": {"duration": 0, "distance": 0},
                    }
                
                # 记录当前使用的坐标
                self.logger.debug(f"当前坐标：起点={self.origin}, 终点={self.destination}")
                
                # 获取驾车路线规划
                driving_data = await self._fetch_driving_route()
                # 获取公交路线规划
                transit_data = await self._fetch_transit_route()
                
                # 记录获取到的数据
                self.logger.debug(f"更新完成：驾车={driving_data}, 公交={transit_data}")

                return {
                    "driving": driving_data,
                    "transit": transit_data,
                }
        except asyncio.TimeoutError:
            self.logger.error("请求高德API超时")
            # 返回空数据而不是抛出异常，避免组件崩溃
            return {
                "driving": {"duration": 0, "distance": 0},
                "transit": {"duration": 0, "distance": 0},
            }
        except Exception as err:
            self.logger.error(f"请求高德API时出错：{err}")
            # 返回空数据而不是抛出异常，避免组件崩溃
            return {
                "driving": {"duration": 0, "distance": 0},
                "transit": {"duration": 0, "distance": 0},
            }
            
    async def _update_entity_locations(self):
        """Update entity locations if entities are used."""
        # 检查起点是否为实体
        if isinstance(self.origin, str) and self.origin.startswith("entity:"):
            entity_id = self.origin.split(":", 1)[1]
            entity_state = self.hass.states.get(entity_id)
            
            if entity_state is None:
                _LOGGER.error(f"无法找到实体：{entity_id}")
                return False
                
            if entity_state.attributes.get("longitude") is None or entity_state.attributes.get("latitude") is None:
                _LOGGER.error(f"实体 {entity_id} 缺少位置属性")
                return False
                
            self.origin = f"{entity_state.attributes.get('longitude')},{entity_state.attributes.get('latitude')}"
            self.logger.debug(f"更新起点坐标：{self.origin}")
        
        # 检查终点是否为实体
        if isinstance(self.destination, str) and self.destination.startswith("entity:"):
            entity_id = self.destination.split(":", 1)[1]
            entity_state = self.hass.states.get(entity_id)
            
            if entity_state is None:
                _LOGGER.error(f"无法找到实体：{entity_id}")
                return False
                
            if entity_state.attributes.get("longitude") is None or entity_state.attributes.get("latitude") is None:
                _LOGGER.error(f"实体 {entity_id} 缺少位置属性")
                return False
                
            self.destination = f"{entity_state.attributes.get('longitude')},{entity_state.attributes.get('latitude')}"
            self.logger.debug(f"更新终点坐标：{self.destination}")
            
        return True

    async def _fetch_driving_route(self):
        """Fetch driving route data from Gaode API."""
        url = "https://restapi.amap.com/v3/direction/driving"
        
        # 确保我们有有效的坐标
        if not self.origin or not self.destination:
            self.logger.error("缺少有效的起点或终点坐标")
            return {"duration": 0, "distance": 0}
            
        # 如果是实体ID格式，需要先更新位置
        if isinstance(self.origin, str) and self.origin.startswith("entity:") or \
           isinstance(self.destination, str) and self.destination.startswith("entity:"):
            await self._update_entity_locations()
            
        # 记录请求信息
        self.logger.debug(f"请求驾车路线：起点={self.origin}, 终点={self.destination}")
        
        params = {
            "key": self.api_key,
            "origin": self.origin,
            "destination": self.destination,
            "extensions": "base",
            "strategy": 0,  # 速度优先
        }

        try:
            async with self.session.get(url, params=params) as response:
                data = await response.json()
                # self.logger.debug(f"高德API响应：{data}")
                
                if data.get("status") == "1":
                    route = data.get("route", {})
                    paths = route.get("paths", [])
                    if paths:
                        path = paths[0]
                        duration = int(path.get("duration", 0))  # 秒
                        distance = int(path.get("distance", 0))  # 米
                        return {
                            "duration": duration,
                            "distance": distance,
                        }
                elif data.get('info') == "INVALID_PARAMS":
                    self.logger.error("参数错误：请检查经纬度顺序（经度在前）或必填字段")
                elif data.get('info') == "INSUFFICIENT_ABROAD_PRIVILEGES":
                    self.logger.error("权限不足：请确认API Key已开通国际坐标权限")
                else:
                    self.logger.error(f"高德API返回错误：{data.get('info')}")
                return {"duration": 0, "distance": 0}
        except Exception as e:
            self.logger.error(f"请求高德API时出错：{e}")
            return {"duration": 0, "distance": 0}

    async def _fetch_transit_route(self):
        """Fetch transit route data from Gaode API."""
        url = "https://restapi.amap.com/v3/direction/transit/integrated"
        
        # 确保我们有有效的坐标
        if not self.origin or not self.destination:
            self.logger.error("缺少有效的起点或终点坐标")
            return {"duration": 0, "distance": 0}
            
        # 如果是实体ID格式，需要先更新位置
        if isinstance(self.origin, str) and self.origin.startswith("entity:") or \
           isinstance(self.destination, str) and self.destination.startswith("entity:"):
            await self._update_entity_locations()
            
        # 记录请求信息
        self.logger.debug(f"请求公交路线：起点={self.origin}, 终点={self.destination}, 城市={self.city}")
        
        params = {
            "key": self.api_key,
            "origin": self.origin,
            "destination": self.destination,
            "city": self.city,
            "cityd": self.city,
            "extensions": "base",
            "strategy": 0,  # 最快捷模式
        }

        try:
            async with self.session.get(url, params=params) as response:
                data = await response.json()
                # self.logger.debug(f"高德API响应：{data}")
                
                # 安全获取API响应数据
                if not isinstance(data, dict):
                    self.logger.error(f"API响应不是字典类型: {type(data)}")
                    return {"duration": 0, "distance": 0}
                
                if data.get("status") == "1":
                    # 确保route是字典类型
                    route = data.get("route", {})
                    if not isinstance(route, dict):
                        self.logger.error(f"路线数据不是字典类型: {type(route)}")
                        return {"duration": 0, "distance": 0}
                        
                    transits = route.get("transits", [])
                    # 确保transits是列表类型
                    if not isinstance(transits, list):
                        self.logger.error(f"公交路线数据不是列表类型: {type(transits)}")
                        return {"duration": 0, "distance": 0}
                        
                    # 直接从route中获取总距离
                    try:
                        total_distance = route.get("distance", 0)
                        if isinstance(total_distance, (str, int, float)):
                            distance = int(total_distance)
                        else:
                            self.logger.error(f"路线总距离格式不正确: {type(total_distance)}")
                            distance = 0
                    except (ValueError, TypeError) as e:
                        self.logger.error(f"处理路线总距离时出错: {e}")
                        distance = 0
                        
                    self.logger.debug(f"从API直接获取的总距离: {distance}米")
                    
                    if transits:
                        transit = transits[0]
                        # 确保transit是字典类型
                        if not isinstance(transit, dict):
                            self.logger.error(f"公交路线详情不是字典类型: {type(transit)}")
                            return {"duration": 0, "distance": 0}
                        
                        # 安全地转换持续时间
                        try:
                            duration_value = transit.get("duration", 0)
                            duration = int(duration_value) if isinstance(duration_value, (str, int, float)) else 0
                        except (ValueError, TypeError):
                            self.logger.error(f"无法转换持续时间值: {transit.get('duration')}")
                            duration = 0
                        
                        self.logger.debug(f"公交路线计算结果：时间={duration}秒, 距离={distance}米")
                        
                        return {
                            "duration": duration,
                            "distance": distance,
                        }
                elif data.get('info') == "INVALID_PARAMS":
                    self.logger.error("参数错误：请检查经纬度顺序（经度在前）或必填字段")
                elif data.get('info') == "INSUFFICIENT_ABROAD_PRIVILEGES":
                    self.logger.error("权限不足：请确认API Key已开通国际坐标权限")
                else:
                    self.logger.error(f"高德API返回错误：{data.get('info')}")
                return {"duration": 0, "distance": 0}
        except Exception as e:
            self.logger.error(f"请求高德API时出错：{e}")
            return {"duration": 0, "distance": 0}
