"""
Database module for Telegram Bot
Handles user profiles, printer settings, presets, and energy tariffs
"""

import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, asdict, field

logger = logging.getLogger(__name__)

# Database file path
DB_FILE = Path("bot_database.json")


@dataclass
class PrinterProfile:
    """Printer profile with specifications"""
    id: str
    name: str
    manufacturer: str
    build_volume_x: int  # mm
    build_volume_y: int  # mm
    build_volume_z: int  # mm
    power_consumption: float  # kW/h average
    nozzle_diameter: float  # mm
    max_nozzle_temp: int  # °C
    max_bed_temp: int  # °C
    filament_types: List[str] = field(default_factory=list)
    is_active: bool = True


@dataclass
class EnergyTariff:
    """Energy tariff configuration"""
    id: str
    name: str
    price_per_kwh: float  # Price per kWh
    currency: str = "USD"
    peak_hours_start: int = 0  # Hour (0-23)
    peak_hours_end: int = 0  # Hour (0-23), 0 means no peak pricing
    peak_multiplier: float = 1.0


@dataclass
class CostPreset:
    """Cost calculation preset"""
    id: str
    name: str
    user_id: str
    base_price: float
    price_per_gram: float
    price_per_hour: float
    support_multiplier: float
    material_prices: Dict[str, float]
    energy_enabled: bool = False
    tariff_id: Optional[str] = None
    is_default: bool = False
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class UserProfile:
    """User profile with settings"""
    user_id: str
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    language_code: str = "en"
    printer_id: Optional[str] = None
    tariff_id: Optional[str] = None
    default_preset_id: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    preferences: Dict[str, Any] = field(default_factory=dict)


class Database:
    """Simple JSON-based database for bot data"""
    
    def __init__(self, db_path: Path = DB_FILE):
        self.db_path = db_path
        self._data: Dict[str, Any] = {
            "users": {},
            "printers": {},
            "tariffs": {},
            "presets": {},
            "sessions": {}
        }
        self._load()
        self._initialize_default_data()
    
    def _load(self):
        """Load database from file"""
        if self.db_path.exists():
            try:
                with open(self.db_path, 'r', encoding='utf-8') as f:
                    self._data = json.load(f)
                logger.info("Database loaded from %s", self.db_path)
            except Exception as e:
                logger.error("Error loading database: %s", e)
    
    def _save(self):
        """Save database to file"""
        try:
            with open(self.db_path, 'w', encoding='utf-8') as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error("Error saving database: %s", e)
    
    def _initialize_default_data(self):
        """Initialize default printers and tariffs"""
        if not self._data["printers"]:
            self._init_default_printers()
        if not self._data["tariffs"]:
            self._init_default_tariffs()
    
    def _init_default_printers(self):
        """Initialize database with popular 3D printers"""
        printers = [
            # Snapmaker
            PrinterProfile(
                id="snapmaker_u1", name="Snapmaker U1", manufacturer="Snapmaker",
                build_volume_x=227, build_volume_y=227, build_volume_z=245,
                power_consumption=0.15, nozzle_diameter=0.4,
                max_nozzle_temp=300, max_bed_temp=100,
                filament_types=["PLA", "PETG", "ABS", "TPU", "Wood", "Carbon Fiber"]
            ),
            PrinterProfile(
                id="snapmaker_2_0_a150", name="Snapmaker 2.0 A150", manufacturer="Snapmaker",
                build_volume_x=160, build_volume_y=160, build_volume_z=145,
                power_consumption=0.12, nozzle_diameter=0.4,
                max_nozzle_temp=275, max_bed_temp=80,
                filament_types=["PLA", "PETG", "ABS", "TPU"]
            ),
            PrinterProfile(
                id="snapmaker_2_0_a250", name="Snapmaker 2.0 A250", manufacturer="Snapmaker",
                build_volume_x=230, build_volume_y=250, build_volume_z=235,
                power_consumption=0.18, nozzle_diameter=0.4,
                max_nozzle_temp=275, max_bed_temp=80,
                filament_types=["PLA", "PETG", "ABS", "TPU"]
            ),
            PrinterProfile(
                id="snapmaker_2_0_a350", name="Snapmaker 2.0 A350", manufacturer="Snapmaker",
                build_volume_x=320, build_volume_y=350, build_volume_z=330,
                power_consumption=0.22, nozzle_diameter=0.4,
                max_nozzle_temp=275, max_bed_temp=80,
                filament_types=["PLA", "PETG", "ABS", "TPU"]
            ),
            
            # Prusa
            PrinterProfile(
                id="prusa_mk4", name="Prusa MK4", manufacturer="Prusa Research",
                build_volume_x=250, build_volume_y=210, build_volume_z=220,
                power_consumption=0.14, nozzle_diameter=0.4,
                max_nozzle_temp=290, max_bed_temp=120,
                filament_types=["PLA", "PETG", "ABS", "TPU", "ASA", "PC", "PVA", "HIPS"]
            ),
            PrinterProfile(
                id="prusa_mini", name="Prusa MINI", manufacturer="Prusa Research",
                build_volume_x=180, build_volume_y=180, build_volume_z=180,
                power_consumption=0.10, nozzle_diameter=0.4,
                max_nozzle_temp=280, max_bed_temp=100,
                filament_types=["PLA", "PETG", "ABS", "TPU", "ASA"]
            ),
            PrinterProfile(
                id="prusa_mk3s", name="Prusa i3 MK3S+", manufacturer="Prusa Research",
                build_volume_x=250, build_volume_y=210, build_volume_z=210,
                power_consumption=0.15, nozzle_diameter=0.4,
                max_nozzle_temp=290, max_bed_temp=120,
                filament_types=["PLA", "PETG", "ABS", "TPU", "ASA", "PC", "PVA", "HIPS"]
            ),
            
            # Creality
            PrinterProfile(
                id="creality_ender3_v3", name="Creality Ender 3 V3", manufacturer="Creality",
                build_volume_x=220, build_volume_y=220, build_volume_z=250,
                power_consumption=0.18, nozzle_diameter=0.4,
                max_nozzle_temp=260, max_bed_temp=100,
                filament_types=["PLA", "PETG", "ABS", "TPU"]
            ),
            PrinterProfile(
                id="creality_ender5", name="Creality Ender 5", manufacturer="Creality",
                build_volume_x=220, build_volume_y=220, build_volume_z=300,
                power_consumption=0.20, nozzle_diameter=0.4,
                max_nozzle_temp=260, max_bed_temp=100,
                filament_types=["PLA", "PETG", "ABS", "TPU"]
            ),
            PrinterProfile(
                id="creality_k1", name="Creality K1", manufacturer="Creality",
                build_volume_x=220, build_volume_y=220, build_volume_z=250,
                power_consumption=0.25, nozzle_diameter=0.4,
                max_nozzle_temp=300, max_bed_temp=100,
                filament_types=["PLA", "PETG", "ABS", "TPU", "ASA", "Carbon Fiber"]
            ),
            
            # Bambu Lab
            PrinterProfile(
                id="bambu_x1c", name="Bambu Lab X1-Carbon", manufacturer="Bambu Lab",
                build_volume_x=256, build_volume_y=256, build_volume_z=256,
                power_consumption=0.28, nozzle_diameter=0.4,
                max_nozzle_temp=320, max_bed_temp=110,
                filament_types=["PLA", "PETG", "ABS", "TPU", "ASA", "PC", "Carbon Fiber", "Nylon"]
            ),
            PrinterProfile(
                id="bambu_p1p", name="Bambu Lab P1P", manufacturer="Bambu Lab",
                build_volume_x=256, build_volume_y=256, build_volume_z=256,
                power_consumption=0.25, nozzle_diameter=0.4,
                max_nozzle_temp=300, max_bed_temp=100,
                filament_types=["PLA", "PETG", "ABS", "TPU", "ASA", "Carbon Fiber"]
            ),
            PrinterProfile(
                id="bambu_a1", name="Bambu Lab A1", manufacturer="Bambu Lab",
                build_volume_x=256, build_volume_y=256, build_volume_z=256,
                power_consumption=0.18, nozzle_diameter=0.4,
                max_nozzle_temp=280, max_bed_temp=100,
                filament_types=["PLA", "PETG", "ABS", "TPU", "ASA"]
            ),
            
            # Anycubic
            PrinterProfile(
                id="anycubic_kobra2", name="Anycubic Kobra 2", manufacturer="Anycubic",
                build_volume_x=250, build_volume_y=250, build_volume_z=260,
                power_consumption=0.18, nozzle_diameter=0.4,
                max_nozzle_temp=260, max_bed_temp=100,
                filament_types=["PLA", "PETG", "ABS", "TPU"]
            ),
            PrinterProfile(
                id="anycubic_photon", name="Anycubic Photon Mono", manufacturer="Anycubic",
                build_volume_x=130, build_volume_y=80, build_volume_z=165,
                power_consumption=0.05, nozzle_diameter=0.0,
                max_nozzle_temp=0, max_bed_temp=0,
                filament_types=["Resin"]
            ),
            
            # Voron
            PrinterProfile(
                id="voron_2.4_250", name="Voron 2.4 R2 250mm", manufacturer="Voron Design",
                build_volume_x=250, build_volume_y=250, build_volume_z=250,
                power_consumption=0.20, nozzle_diameter=0.4,
                max_nozzle_temp=350, max_bed_temp=120,
                filament_types=["PLA", "PETG", "ABS", "TPU", "ASA", "PC", "Nylon", "Carbon Fiber"]
            ),
            PrinterProfile(
                id="voron_2.4_350", name="Voron 2.4 R2 350mm", manufacturer="Voron Design",
                build_volume_x=350, build_volume_y=350, build_volume_z=350,
                power_consumption=0.28, nozzle_diameter=0.4,
                max_nozzle_temp=350, max_bed_temp=120,
                filament_types=["PLA", "PETG", "ABS", "TPU", "ASA", "PC", "Nylon", "Carbon Fiber"]
            ),
            
            # Ultimaker
            PrinterProfile(
                id="ultimaker_s3", name="Ultimaker S3", manufacturer="Ultimaker",
                build_volume_x=230, build_volume_y=190, build_volume_z=200,
                power_consumption=0.22, nozzle_diameter=0.4,
                max_nozzle_temp=280, max_bed_temp=140,
                filament_types=["PLA", "PETG", "ABS", "TPU", "ASA", "PC", "Nylon", "PVA", "HIPS"]
            ),
            PrinterProfile(
                id="ultimaker_s5", name="Ultimaker S5", manufacturer="Ultimaker",
                build_volume_x=330, build_volume_y=240, build_volume_z=300,
                power_consumption=0.28, nozzle_diameter=0.4,
                max_nozzle_temp=280, max_bed_temp=140,
                filament_types=["PLA", "PETG", "ABS", "TPU", "ASA", "PC", "Nylon", "PVA", "HIPS"]
            ),
        ]
        
        for printer in printers:
            self._data["printers"][printer.id] = asdict(printer)
        
        logger.info("Initialized %d default printers", len(printers))
    
    def _init_default_tariffs(self):
        """Initialize default energy tariffs"""
        tariffs = [
            EnergyTariff(
                id="us_average", name="US Average", 
                price_per_kwh=0.15, currency="USD"
            ),
            EnergyTariff(
                id="eu_average", name="EU Average", 
                price_per_kwh=0.28, currency="EUR"
            ),
            EnergyTariff(
                id="uk_average", name="UK Average", 
                price_per_kwh=0.34, currency="GBP"
            ),
            EnergyTariff(
                id="germany", name="Germany", 
                price_per_kwh=0.40, currency="EUR"
            ),
            EnergyTariff(
                id="france", name="France", 
                price_per_kwh=0.25, currency="EUR"
            ),
            EnergyTariff(
                id="spain_peak", name="Spain (Peak Hours)", 
                price_per_kwh=0.20, currency="EUR",
                peak_hours_start=10, peak_hours_end=14, peak_multiplier=1.5
            ),
            EnergyTariff(
                id="custom", name="Custom Tariff", 
                price_per_kwh=0.20, currency="USD"
            ),
        ]
        
        for tariff in tariffs:
            self._data["tariffs"][tariff.id] = asdict(tariff)
        
        logger.info("Initialized %d default tariffs", len(tariffs))
    
    # ------------------- User Operations -------------------
    
    def get_user(self, user_id: str) -> Optional[UserProfile]:
        """Get user by ID"""
        if user_id in self._data["users"]:
            data = self._data["users"][user_id]
            return UserProfile(**data)
        return None
    
    def create_or_update_user(self, user_id: str, **kwargs) -> UserProfile:
        """Create or update user profile"""
        now = datetime.now().isoformat()
        
        if user_id in self._data["users"]:
            data = self._data["users"][user_id]
            data.update(kwargs)
            data["updated_at"] = now
            user = UserProfile(**data)
        else:
            user = UserProfile(user_id=user_id, **kwargs)
            user.created_at = now
            user.updated_at = now
        
        self._data["users"][user_id] = asdict(user)
        self._save()
        return user
    
    def update_user_printer(self, user_id: str, printer_id: str) -> bool:
        """Update user's default printer"""
        if user_id not in self._data["users"]:
            return False
        self._data["users"][user_id]["printer_id"] = printer_id
        self._data["users"][user_id]["updated_at"] = datetime.now().isoformat()
        self._save()
        return True
    
    def update_user_tariff(self, user_id: str, tariff_id: str) -> bool:
        """Update user's default tariff"""
        if user_id not in self._data["users"]:
            return False
        self._data["users"][user_id]["tariff_id"] = tariff_id
        self._data["users"][user_id]["updated_at"] = datetime.now().isoformat()
        self._save()
        return True
    
    def update_user_preferences(self, user_id: str, preferences: Dict[str, Any]) -> bool:
        """Update user preferences"""
        if user_id not in self._data["users"]:
            return False
        self._data["users"][user_id]["preferences"].update(preferences)
        self._data["users"][user_id]["updated_at"] = datetime.now().isoformat()
        self._save()
        return True
    
    # ------------------- Printer Operations -------------------
    
    def get_printer(self, printer_id: str) -> Optional[PrinterProfile]:
        """Get printer by ID"""
        if printer_id in self._data["printers"]:
            data = self._data["printers"][printer_id]
            return PrinterProfile(**data)
        return None
    
    def get_all_printers(self, active_only: bool = True) -> List[PrinterProfile]:
        """Get all printers"""
        printers = []
        for data in self._data["printers"].values():
            if active_only and not data.get("is_active", True):
                continue
            printers.append(PrinterProfile(**data))
        return sorted(printers, key=lambda p: (p.manufacturer, p.name))
    
    def get_printers_by_manufacturer(self, manufacturer: str) -> List[PrinterProfile]:
        """Get printers by manufacturer"""
        return [p for p in self.get_all_printers() if p.manufacturer.lower() == manufacturer.lower()]
    
    def add_printer(self, printer: PrinterProfile) -> bool:
        """Add custom printer"""
        if printer.id in self._data["printers"]:
            return False
        self._data["printers"][printer.id] = asdict(printer)
        self._save()
        return True
    
    # ------------------- Tariff Operations -------------------
    
    def get_tariff(self, tariff_id: str) -> Optional[EnergyTariff]:
        """Get tariff by ID"""
        if tariff_id in self._data["tariffs"]:
            data = self._data["tariffs"][tariff_id]
            return EnergyTariff(**data)
        return None
    
    def get_all_tariffs(self) -> List[EnergyTariff]:
        """Get all tariffs"""
        return [EnergyTariff(**data) for data in self._data["tariffs"].values()]
    
    def create_custom_tariff(self, user_id: str, name: str, price_per_kwh: float, 
                            currency: str = "USD") -> EnergyTariff:
        """Create custom tariff for user"""
        tariff_id = f"custom_{user_id}_{int(datetime.now().timestamp())}"
        tariff = EnergyTariff(
            id=tariff_id, name=name, 
            price_per_kwh=price_per_kwh, currency=currency
        )
        self._data["tariffs"][tariff_id] = asdict(tariff)
        self._save()
        return tariff
    
    # ------------------- Preset Operations -------------------
    
    def get_preset(self, preset_id: str) -> Optional[CostPreset]:
        """Get preset by ID"""
        if preset_id in self._data["presets"]:
            data = self._data["presets"][preset_id]
            return CostPreset(**data)
        return None
    
    def get_user_presets(self, user_id: str) -> List[CostPreset]:
        """Get all presets for a user"""
        presets = []
        for data in self._data["presets"].values():
            if data["user_id"] == user_id:
                presets.append(CostPreset(**data))
        return sorted(presets, key=lambda p: (not p.is_default, p.name))
    
    def create_preset(self, preset: CostPreset) -> bool:
        """Create new preset"""
        if preset.id in self._data["presets"]:
            return False
        
        # If this is set as default, unset other defaults for this user
        if preset.is_default:
            for data in self._data["presets"].values():
                if data["user_id"] == preset.user_id:
                    data["is_default"] = False
        
        self._data["presets"][preset.id] = asdict(preset)
        self._save()
        return True
    
    def update_preset(self, preset_id: str, **kwargs) -> bool:
        """Update existing preset"""
        if preset_id not in self._data["presets"]:
            return False
        
        data = self._data["presets"][preset_id]
        data.update(kwargs)
        
        # If setting as default, unset others
        if kwargs.get("is_default"):
            for d in self._data["presets"].values():
                if d["user_id"] == data["user_id"] and d["id"] != preset_id:
                    d["is_default"] = False
        
        self._save()
        return True
    
    def delete_preset(self, preset_id: str, user_id: str) -> bool:
        """Delete preset"""
        if preset_id not in self._data["presets"]:
            return False
        if self._data["presets"][preset_id]["user_id"] != user_id:
            return False
        del self._data["presets"][preset_id]
        self._save()
        return True
    
    def get_user_default_preset(self, user_id: str) -> Optional[CostPreset]:
        """Get user's default preset"""
        for data in self._data["presets"].values():
            if data["user_id"] == user_id and data.get("is_default", False):
                return CostPreset(**data)
        return None
    
    # ------------------- Session Operations -------------------
    
    def save_session(self, session_id: str, data: Dict[str, Any]) -> None:
        """Save session data"""
        self._data["sessions"][session_id] = {
            **data,
            "timestamp": datetime.now().isoformat()
        }
        self._save()
    
    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get session data"""
        return self._data["sessions"].get(session_id)
    
    def delete_session(self, session_id: str) -> bool:
        """Delete session"""
        if session_id in self._data["sessions"]:
            del self._data["sessions"][session_id]
            self._save()
            return True
        return False
    
    def cleanup_old_sessions(self, max_age_hours: int = 8) -> int:
        """Remove old sessions"""
        now = datetime.now()
        deleted = 0
        
        expired = []
        for sid, data in self._data["sessions"].items():
            try:
                timestamp = datetime.fromisoformat(data.get("timestamp", ""))
                if (now - timestamp).total_seconds() > max_age_hours * 3600:
                    expired.append(sid)
            except:
                expired.append(sid)
        
        for sid in expired:
            del self._data["sessions"][sid]
            deleted += 1
        
        if deleted > 0:
            self._save()
        
        return deleted


# Global database instance
db = Database()
