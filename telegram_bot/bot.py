"""
Telegram Bot for 3MF Converter and Print Cost Calculator
Supports: file conversion, cost calculation, mini-app integration,
user profiles, printer selection, energy tariffs, and presets
"""

import os
import re
import json
import uuid
import time
import logging
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional, Dict, Any
from dataclasses import dataclass, field

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    FSInputFile, BufferedInputFile, ReplyKeyboardMarkup, 
    InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiohttp import web as aiohttp_web

from database import db, PrinterProfile, EnergyTariff, CostPreset

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
WEB_APP_URL = os.getenv("WEB_APP_URL", "https://your-domain.com")  # URL for Mini App
API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", "8080"))

UPLOAD_FOLDER = Path("uploads")
UPLOAD_FOLDER.mkdir(exist_ok=True)

# Default pricing configuration (used when no preset is selected)
DEFAULT_PRICING_CONFIG = {
    "base_price": 5.0,  # Base setup fee in USD
    "price_per_gram": 0.05,  # Price per gram of filament
    "price_per_hour": 2.0,  # Price per hour of printing
    "support_multiplier": 1.2,  # Multiplier if supports are needed
    "material_prices": {  # Price per gram by material type
        "PLA": 0.03,
        "PETG": 0.04,
        "ABS": 0.05,
        "TPU": 0.06,
        "DEFAULT": 0.04,
    }
}

FILAMENT_PROFILES_FILE = Path("filament_types.3mf")
TARGET_FILAMENTS = 4
DEFAULT_FILAMENT_PROFILE = 'Snapmaker PLA SnapSpeed @U1'

_SESSION_RE = re.compile(r'^[0-9a-f]{32}$')
_COLOR_RE = re.compile(r'^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$')

# Session storage: session_id -> {filename, filaments, timestamp, user_id}
_sessions: Dict[str, Dict[str, Any]] = {}


@dataclass
class FilamentInfo:
    """Information about a filament from a 3MF file"""
    id: str
    color: str
    type: str
    used_m: float = 0.0
    used_g: float = 0.0


@dataclass
class ConversionResult:
    """Result of a 3MF conversion"""
    success: bool
    session_id: Optional[str] = None
    filaments: list = field(default_factory=list)
    error: Optional[str] = None
    download_path: Optional[Path] = None
    download_name: Optional[str] = None
    cost_estimate: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Filament profiles loader
# ---------------------------------------------------------------------------
AVAILABLE_FILAMENTS = []

def load_filament_profiles():
    """Load available filament profiles from template file"""
    global AVAILABLE_FILAMENTS
    try:
        with zipfile.ZipFile(FILAMENT_PROFILES_FILE, 'r') as z:
            settings = json.loads(z.read('Metadata/project_settings.config').decode('utf-8'))
            for t, sid in zip(settings.get('filament_type', []), settings.get('filament_settings_id', [])):
                AVAILABLE_FILAMENTS.append({'type': t, 'settings_id': sid})
        logger.info("Loaded %d filament profiles", len(AVAILABLE_FILAMENTS))
    except Exception as e:
        logger.warning("Could not load filament profiles (%s) -- using fallback defaults", e)
        AVAILABLE_FILAMENTS = [
            {'type': 'PLA', 'settings_id': DEFAULT_FILAMENT_PROFILE},
            {'type': 'PETG', 'settings_id': 'Snapmaker PETG HF'},
            {'type': 'ABS', 'settings_id': 'Generic ABS'},
            {'type': 'TPU', 'settings_id': 'Generic TPU'},
        ]
    return AVAILABLE_FILAMENTS


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------
def normalize_color(color: str) -> str:
    """Normalize color to #RRGGBB format"""
    if not color:
        return '#000000'
    c = color.lstrip('#')
    if len(c) == 8:
        c = c[:6]
    if len(c) != 6:
        return '#000000'
    try:
        int(c, 16)
    except ValueError:
        return '#000000'
    return f'#{c.upper()}'


def parse_3mf_filaments(filepath: Path) -> list[FilamentInfo]:
    """Parse filament information from a 3MF file"""
    filaments = []
    try:
        with zipfile.ZipFile(filepath, 'r') as z:
            names = z.namelist()
            
            # Try slice_info.config first
            if 'Metadata/slice_info.config' in names:
                root = ET.fromstring(z.read('Metadata/slice_info.config').decode('utf-8'))
                for fil in root.findall('.//filament'):
                    filaments.append(FilamentInfo(
                        id=fil.get('id', '1'),
                        color=normalize_color(fil.get('color', '')),
                        type=fil.get('type') or 'PLA',
                        used_m=float(fil.get('used_m', 0) or 0),
                        used_g=float(fil.get('used_g', 0) or 0),
                    ))
            
            # Fallback to project_settings.config
            if not filaments and 'Metadata/project_settings.config' in names:
                cfg = json.loads(z.read('Metadata/project_settings.config').decode('utf-8'))
                colors = cfg.get('filament_colour', [])
                types = cfg.get('filament_type', [])
                for i, color in enumerate(colors):
                    filaments.append(FilamentInfo(
                        id=str(i + 1),
                        color=normalize_color(color),
                        type=types[i] if i < len(types) else 'PLA',
                    ))
                    
            # Try to get model metadata for weight estimation
            if 'Metadata/model_settings.config' in names:
                model_root = ET.fromstring(z.read('Metadata/model_settings.config').decode('utf-8'))
                for vol in model_root.findall('.//metadata[@key="volume"]'):
                    volume_cm3 = float(vol.get('value', 0) or 0)
                    # Estimate weight: PLA density ~1.24 g/cm³
                    estimated_weight = volume_cm3 * 1.24
                    if filaments:
                        # Distribute weight among filaments
                        per_filament = estimated_weight / len(filaments)
                        for f in filaments:
                            if f.used_g == 0:
                                f.used_g = per_filament
    except Exception as e:
        logger.error("Error parsing filaments from %s: %s", filepath, e)
    return filaments


def get_pricing_config(user_id: str) -> Dict[str, Any]:
    """Get pricing configuration for user (from preset or default)"""
    user = db.get_user(user_id)
    if user and user.default_preset_id:
        preset = db.get_preset(user.default_preset_id)
        if preset:
            return {
                "base_price": preset.base_price,
                "price_per_gram": preset.price_per_gram,
                "price_per_hour": preset.price_per_hour,
                "support_multiplier": preset.support_multiplier,
                "material_prices": preset.material_prices,
                "energy_enabled": preset.energy_enabled,
                "tariff_id": preset.tariff_id,
            }
    return DEFAULT_PRICING_CONFIG.copy()


def calculate_energy_cost(print_time_hours: float, printer_id: Optional[str], 
                         tariff_id: Optional[str]) -> Dict[str, Any]:
    """Calculate energy cost based on printer consumption and tariff"""
    if not printer_id or not tariff_id:
        return {"energy_kwh": 0, "energy_cost": 0, "currency": "USD"}
    
    printer = db.get_printer(printer_id)
    tariff = db.get_tariff(tariff_id)
    
    if not printer or not tariff:
        return {"energy_kwh": 0, "energy_cost": 0, "currency": tariff.currency if tariff else "USD"}
    
    # Calculate energy consumption
    energy_kwh = printer.power_consumption * print_time_hours
    
    # Apply peak hours if configured
    multiplier = 1.0
    if tariff.peak_hours_start != tariff.peak_hours_end and tariff.peak_multiplier > 1.0:
        # Simplified: assume printing during peak hours
        multiplier = tariff.peak_multiplier
    
    energy_cost = energy_kwh * tariff.price_per_kwh * multiplier
    
    return {
        "energy_kwh": round(energy_kwh, 2),
        "energy_cost": round(energy_cost, 2),
        "currency": tariff.currency,
        "printer_name": printer.name,
        "tariff_name": tariff.name,
    }


def calculate_print_cost(
    filaments: list[FilamentInfo],
    has_supports: bool = False,
    print_time_hours: float = 0.0,
    user_id: Optional[str] = None,
    printer_id: Optional[str] = None,
    tariff_id: Optional[str] = None
) -> Dict[str, Any]:
    """Calculate estimated print cost with optional energy cost"""
    # Get pricing config for user
    pricing = get_pricing_config(user_id) if user_id else DEFAULT_PRICING_CONFIG
    
    total_weight = sum(f.used_g for f in filaments)
    
    # Calculate material cost based on type
    material_cost = 0.0
    for f in filaments:
        price_per_gram = pricing["material_prices"].get(
            f.type, 
            pricing["material_prices"].get("DEFAULT", 0.04)
        )
        material_cost += f.used_g * price_per_gram
    
    # If no weight info, estimate from count
    if total_weight == 0:
        total_weight = len(filaments) * 50  # Assume 50g per filament as fallback
        material_cost = total_weight * pricing.get("price_per_gram", 0.05)
    
    # Time-based cost
    time_cost = print_time_hours * pricing.get("price_per_hour", 2.0)
    
    # Support multiplier
    subtotal = pricing.get("base_price", 5.0) + material_cost + time_cost
    if has_supports:
        subtotal *= pricing.get("support_multiplier", 1.2)
    
    # Energy cost (if enabled)
    energy_data = {"energy_kwh": 0, "energy_cost": 0, "currency": "USD"}
    if pricing.get("energy_enabled", False):
        energy_tariff = tariff_id or pricing.get("tariff_id")
        energy_data = calculate_energy_cost(print_time_hours, printer_id, energy_tariff)
    
    total = subtotal + energy_data["energy_cost"]
    
    result = {
        "base_price": pricing.get("base_price", 5.0),
        "material_cost": round(material_cost, 2),
        "time_cost": round(time_cost, 2),
        "total_weight_g": round(total_weight, 1),
        "print_time_hours": print_time_hours,
        "has_supports": has_supports,
        "energy_kwh": energy_data.get("energy_kwh", 0),
        "energy_cost": energy_data.get("energy_cost", 0),
        "total": round(total, 2),
        "currency": energy_data.get("currency", "USD"),
    }
    
    if energy_data.get("printer_name"):
        result["printer_name"] = energy_data["printer_name"]
    if energy_data.get("tariff_name"):
        result["tariff_name"] = energy_data["tariff_name"]
    
    return result


def _safe_path(filename: str) -> Optional[Path]:
    """Safely resolve a path within the upload folder"""
    safe_dir = UPLOAD_FOLDER.resolve()
    candidate = (safe_dir / filename).resolve()
    try:
        candidate.relative_to(safe_dir)
        return candidate
    except ValueError:
        return None


def cleanup_old_files(max_age_hours: int = 8) -> None:
    """Remove old uploaded files"""
    now = time.time()
    cutoff = max_age_hours * 3600
    try:
        for name in os.listdir(UPLOAD_FOLDER):
            path = UPLOAD_FOLDER / name
            if path.is_file() and (now - path.stat().st_mtime) > cutoff:
                path.unlink()
                logger.debug("Deleted old upload: %s", name)
        
        # Cleanup expired sessions
        expired = [sid for sid, data in _sessions.items() 
                   if (time.time() - data.get('timestamp', 0)) > cutoff]
        for sid in expired:
            _sessions.pop(sid, None)
    except Exception as e:
        logger.error("Cleanup error: %s", e)


# ---------------------------------------------------------------------------
# Conversion logic
# ---------------------------------------------------------------------------
def convert_3mf_to_u1(
    input_path: Path,
    session_id: str,
    user_colors: Dict[str, Dict[str, str]],
    original_filaments: list[FilamentInfo]
) -> ConversionResult:
    """Convert a 3MF file to U1 format"""
    valid_ids = {f.id for f in original_filaments}
    
    # Validate user input
    for fid, conf in user_colors.items():
        if fid not in valid_ids:
            return ConversionResult(success=False, error=f'Unknown filament ID: {fid}')
        color = conf.get('color', '')
        ftype = conf.get('type', '')
        if not _COLOR_RE.match(color):
            return ConversionResult(success=False, error=f'Invalid color: {color}')
        valid_types = {f['type'] for f in AVAILABLE_FILAMENTS}
        if ftype not in valid_types:
            return ConversionResult(success=False, error=f'Invalid filament type: {ftype}')
    
    # Check for supports in original file
    has_supports = False
    try:
        with zipfile.ZipFile(input_path, 'r') as z:
            orig_settings = json.loads(z.read('Metadata/project_settings.config').decode('utf-8'))
            diff = orig_settings.get('different_settings_to_system', [])
            has_supports = any(isinstance(s, str) and 'enable_support' in s for s in diff)
    except Exception as e:
        logger.error("Could not read project settings: %s", e)
    
    # Select template
    template_name = 'u1_template_supports.3mf' if has_supports else 'u1_template.3mf'
    template_path = Path(template_name)
    
    if not template_path.exists():
        return ConversionResult(success=False, error='Server template missing')
    
    try:
        with zipfile.ZipFile(template_path, 'r') as z:
            u1_settings = json.loads(z.read('Metadata/project_settings.config').decode('utf-8'))
    except Exception as e:
        logger.error("Could not read template %s: %s", template_name, e)
        return ConversionResult(success=False, error='Server template read error')
    
    output_path = _safe_path(f'{session_id}_U1_Ready.3mf')
    if output_path is None:
        return ConversionResult(success=False, error='Internal path error')
    
    try:
        with zipfile.ZipFile(input_path, 'r') as zin, \
             zipfile.ZipFile(output_path, 'w', compression=zipfile.ZIP_DEFLATED) as zout:
            
            # Modify slice_info.config
            xml_str = zin.read('Metadata/slice_info.config').decode('utf-8')
            xml_str = re.sub(
                r'key="printer_model_id" value="[^"]*"',
                'key="printer_model_id" value="Snapmaker U1"',
                xml_str,
            )
            root = ET.fromstring(xml_str)
            filaments_parent = root.find('.//plate') or root
            
            existing_nodes = filaments_parent.findall('filament')
            id_mapping: Dict[str, str] = {}
            new_id_counter = 1
            
            for node in list(existing_nodes):
                old_id = node.get('id')
                if old_id not in user_colors:
                    filaments_parent.remove(node)
                else:
                    conf = user_colors[old_id]
                    id_mapping[old_id] = str(new_id_counter)
                    node.set('id', str(new_id_counter))
                    node.set('color', conf['color'])
                    node.set('type', conf['type'])
                    new_id_counter += 1
            
            # Pad to TARGET_FILAMENTS
            while new_id_counter <= TARGET_FILAMENTS:
                dummy = ET.SubElement(filaments_parent, 'filament')
                dummy.set('id', str(new_id_counter))
                dummy.set('type', 'PLA')
                dummy.set('color', '#FFFFFFFF')
                dummy.set('used_m', '0')
                dummy.set('used_g', '0')
                new_id_counter += 1
            
            modified_slice_info = ET.tostring(root, encoding='utf-8', xml_declaration=True)
            
            # Modify model_settings.config
            model_root = ET.fromstring(zin.read('Metadata/model_settings.config').decode('utf-8'))
            for meta in model_root.findall('.//metadata[@key="extruder"]'):
                old_ext = meta.get('value')
                if old_ext in id_mapping:
                    meta.set('value', id_mapping[old_ext])
            modified_model_settings = ET.tostring(model_root, encoding='utf-8', xml_declaration=True)
            
            # Build project_settings.config
            combined = u1_settings.copy()
            new_colors = []
            new_types = []
            
            for fil in original_filaments:
                fid = fil.id
                if fid not in user_colors:
                    continue
                color = user_colors[fid]['color']
                ftype = user_colors[fid]['type']
                color = (color + 'FF') if len(color) == 7 else color
                new_colors.append(color.upper())
                new_types.append(ftype)
            
            while len(new_colors) < TARGET_FILAMENTS:
                new_colors.append('#FFFFFFFF')
                new_types.append('PLA')
            
            combined['filament_colour'] = new_colors
            combined['filament_type'] = new_types
            
            profile_map = {f['type']: f['settings_id'] for f in AVAILABLE_FILAMENTS}
            default_profile = AVAILABLE_FILAMENTS[0]['settings_id'] if AVAILABLE_FILAMENTS else DEFAULT_FILAMENT_PROFILE
            combined['filament_settings_id'] = [
                profile_map.get(t, default_profile) for t in new_types
            ]
            
            # Normalize arrays
            for key, val in combined.items():
                if key.startswith('filament_') and isinstance(val, list) and 0 < len(val) != TARGET_FILAMENTS:
                    if len(val) < TARGET_FILAMENTS:
                        val.extend([val[-1]] * (TARGET_FILAMENTS - len(val)))
                    else:
                        combined[key] = val[:TARGET_FILAMENTS]
            
            combined_bytes = json.dumps(combined, indent=4, ensure_ascii=False).encode('utf-8')
            
            # Copy all members
            for item in zin.infolist():
                safe_name = item.filename.lstrip('/')
                if safe_name.startswith('..'):
                    logger.warning("Skipping suspicious ZIP entry: %s", item.filename)
                    continue
                
                if item.filename == 'Metadata/project_settings.config':
                    zout.writestr(item, combined_bytes)
                elif item.filename == 'Metadata/slice_info.config':
                    zout.writestr(item, modified_slice_info)
                elif item.filename == 'Metadata/model_settings.config':
                    zout.writestr(item, modified_model_settings)
                else:
                    zout.writestr(item, zin.read(item.filename))
        
        # Calculate cost estimate
        converted_filaments = []
        for fil in original_filaments:
            if fil.id in user_colors:
                converted_filaments.append(FilamentInfo(
                    id=fil.id,
                    color=user_colors[fil.id]['color'],
                    type=user_colors[fil.id]['type'],
                    used_g=fil.used_g,
                    used_m=fil.used_m,
                ))
        
        cost_estimate = calculate_print_cost(converted_filaments, has_supports)
        
        return ConversionResult(
            success=True,
            session_id=session_id,
            download_path=output_path,
            download_name=f"{session_id[:8]}-U1.3mf",
            cost_estimate=cost_estimate,
        )
        
    except Exception as e:
        logger.error("Conversion error [%s]: %s", session_id, e, exc_info=True)
        if output_path.exists():
            try:
                output_path.unlink()
            except OSError:
                pass
        return ConversionResult(success=False, error=f'Conversion failed: {str(e)}')


# ---------------------------------------------------------------------------
# Bot setup
# ---------------------------------------------------------------------------
def create_bot():
    """Create bot instance only if token is provided"""
    if not BOT_TOKEN:
        logger.warning("BOT_TOKEN not set. Bot functionality will be disabled.")
        return None
    try:
        return Bot(token=BOT_TOKEN)
    except Exception as e:
        logger.error(f"Failed to initialize bot: {e}")
        return None

bot = create_bot()
dp = Dispatcher() if bot else None


def get_main_keyboard() -> ReplyKeyboardMarkup:
    """Get main keyboard with commands"""
    builder = ReplyKeyboardBuilder()
    builder.button(text="📤 Конвертировать файл", command="convert")
    builder.button(text="💰 Калькулятор стоимости", command="calc")
    builder.button(text="⚙️ Настройки", command="settings")
    builder.button(text="👤 Личный кабинет", command="profile")
    builder.button(text="📱 Mini App", web_app=WebAppInfo(url=WEB_APP_URL))
    builder.button(text="ℹ️ Помощь", command="help")
    builder.adjust(2, 2)
    return builder.as_markup(resize_keyboard=True)


def get_filament_selection_keyboard(filaments: list[FilamentInfo], session_id: str) -> InlineKeyboardMarkup:
    """Get inline keyboard for filament selection"""
    builder = InlineKeyboardBuilder()
    
    for i, fil in enumerate(filaments[:4]):  # Max 4 filaments shown
        builder.button(
            text=f"🎨 {fil.type} - {fil.color}",
            callback_data=f"filament_{session_id}_{fil.id}"
        )
    
    builder.button(text="✅ Готово", callback_data=f"done_{session_id}")
    builder.button(text="❌ Отмена", callback_data=f"cancel_{session_id}")
    
    builder.adjust(2)
    return builder.as_markup()


def get_cost_options_keyboard(session_id: str) -> InlineKeyboardMarkup:
    """Get keyboard for cost calculation options"""
    builder = InlineKeyboardBuilder()
    builder.button(text="⏱️ Указать время печати", callback_data=f"time_{session_id}")
    builder.button(text="🔄 Пересчитать", callback_data=f"recalc_{session_id}")
    builder.button(text="📥 Скачать", callback_data=f"download_{session_id}")
    builder.button(text="❌ Закрыть", callback_data=f"close_{session_id}")
    builder.adjust(2, 2)
    return builder.as_markup()


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------
if bot and dp:
    @dp.message(CommandStart())
    async def cmd_start(message: types.Message):
        """Handle /start command"""
        await message.answer(
            f"👋 Привет! Я бот для конвертации .3mf файлов и расчета стоимости 3D печати.\n\n"
            f"🔧 <b>Что я умею:</b>\n"
            f"• Конвертировать файлы из Bambu Lab в Snapmaker U1\n"
            f"• Рассчитывать стоимость печати\n"
            f"• Работать через Mini App\n\n"
            f"Нажмите кнопку ниже или используйте меню:",
            reply_markup=get_main_keyboard(),
            parse_mode="HTML"
        )


    @dp.message(Command("help"))
    async def cmd_help(message: types.Message):
        """Handle /help command"""
        help_text = (
            "ℹ️ <b>Помощь по использованию бота</b>\n\n"
            "📤 <b>Конвертация файла:</b>\n"
            "1. Нажмите /convert или кнопку \"Конвертировать файл\"\n"
            "2. Отправьте .3mf файл\n"
            "3. Настройте филаменты\n"
            "4. Получите конвертированный файл\n\n"
            "💰 <b>Расчет стоимости:</b>\n"
            "• Автоматически рассчитывается при конвертации\n"
            "• Учитывает вес материала, время печати, тип филамента\n"
            "• Можно указать точное время печати вручную\n\n"
            "📱 <b>Mini App:</b>\n"
            "• Полноценный веб-интерфейс прямо в Telegram\n"
            "• Удобная настройка всех параметров\n"
            "• Визуальный предпросмотр\n\n"
            "📁 <b>Поддерживаемые форматы:</b>\n"
            "• Только .3mf файлы (макс. 200MB)\n"
            "• Файлы автоматически удаляются через 8 часов"
        )
        await message.answer(help_text, parse_mode="HTML")


    @dp.message(Command("convert"))
    async def cmd_convert(message: types.Message):
        """Handle /convert command"""
        await message.answer(
            "📤 <b>Отправьте .3mf файл для конвертации</b>\n\n"
            "Максимальный размер: 200MB\n"
            "Файл будет автоматически проанализирован.",
            parse_mode="HTML"
        )


    @dp.message(Command("calc"))
    async def cmd_calc(message: types.Message):
        """Handle /calc command"""
        await message.answer(
            "💰 <b>Калькулятор стоимости</b>\n\n"
            "Для расчета стоимости отправьте .3mf файл\n"
            "или используйте параметры:\n\n"
            "/calc_weight 100 PLA - расчет по весу (100г, PLA)\n"
            "/calc_time 5.5 - добавить время печати (5.5 часов)",
            parse_mode="HTML"
        )


    @dp.message(Command("settings"))
    async def cmd_settings(message: types.Message):
        """Handle /settings command - printer and tariff selection"""
        user_id = str(message.from_user.id)
        
        # Create or update user profile
        user = db.create_or_update_user(
            user_id=user_id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
            language_code=message.from_user.language_code
        )
        
        # Get all printers grouped by manufacturer
        printers = db.get_all_printers()
        manufacturers = {}
        for p in printers:
            if p.manufacturer not in manufacturers:
                manufacturers[p.manufacturer] = []
            manufacturers[p.manufacturer].append(p)
        
        # Build keyboard with manufacturers
        builder = InlineKeyboardBuilder()
        
        for manufacturer in sorted(manufacturers.keys()):
            builder.button(
                text=f"🖨️ {manufacturer}",
                callback_data=f"manuf_{manufacturer}"
            )
        
        # Show current printer
        current_printer = ""
        if user.printer_id:
            printer = db.get_printer(user.printer_id)
            if printer:
                current_printer = f"\n\n✅ Текущий принтер: <b>{printer.name}</b>"
        
        builder.button(text="⚡ Тарифы на энергию", callback_data="tariffs_menu")
        builder.button(text="💾 Пресеты калькулятора", callback_data="presets_menu")
        builder.adjust(1)
        
        await message.answer(
            f"⚙️ <b>Настройки</b>{current_printer}\n\n"
            f"Выберите производителя вашего принтера:",
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )


    @dp.message(Command("profile"))
    async def cmd_profile(message: types.Message):
        """Handle /profile command - show user profile"""
        user_id = str(message.from_user.id)
        
        user = db.get_user(user_id)
        if not user:
            user = db.create_or_update_user(
                user_id=user_id,
                username=message.from_user.username,
                first_name=message.from_user.first_name,
                last_name=message.from_user.last_name
            )
        
        # Get current printer info
        printer_info = "Не выбран"
        if user.printer_id:
            printer = db.get_printer(user.printer_id)
            if printer:
                printer_info = (
                    f"<b>{printer.name}</b>\n"
                    f"• Объем: {printer.build_volume_x}×{printer.build_volume_y}×{printer.build_volume_z} мм\n"
                    f"• Потребление: {printer.power_consumption} кВт/ч\n"
                    f"• Сопло: {printer.nozzle_diameter} мм"
                )
        
        # Get current tariff info
        tariff_info = "Не выбран"
        if user.tariff_id:
            tariff = db.get_tariff(user.tariff_id)
            if tariff:
                tariff_info = (
                    f"<b>{tariff.name}</b>\n"
                    f"• Цена: {tariff.price_per_kwh} {tariff.currency}/кВт·ч"
                )
        
        # Get default preset info
        preset_info = "Не выбран"
        if user.default_preset_id:
            preset = db.get_preset(user.default_preset_id)
            if preset:
                preset_info = f"<b>{preset.name}</b>"
        
        # Build profile keyboard
        builder = InlineKeyboardBuilder()
        builder.button(text="⚙️ Изменить настройки", callback_data="settings_from_profile")
        builder.button(text="💾 Мои пресеты", callback_data="my_presets")
        builder.adjust(1)
        
        await message.answer(
            f"👤 <b>Личный кабинет</b>\n\n"
            f"📛 Имя: {user.first_name or 'N/A'}\n"
            f"🔗 Username: @{user.username or 'N/A'}\n\n"
            f"🖨️ <b>Принтер:</b>\n{printer_info}\n\n"
            f"⚡ <b>Тариф:</b>\n{tariff_info}\n\n"
            f"💾 <b>Пресет по умолчанию:</b>\n{preset_info}\n\n"
            f"📅 Регистрация: {user.created_at[:10]}",
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )


    @dp.callback_query(F.data.startswith("manuf_"))
    async def handle_manufacturer_select(callback: types.CallbackQuery):
        """Handle manufacturer selection"""
        user_id = str(callback.from_user.id)
        manufacturer = callback.data.replace("manuf_", "")
        
        printers = db.get_printers_by_manufacturer(manufacturer)
        
        builder = InlineKeyboardBuilder()
        for printer in printers:
            builder.button(
                text=f"🖨️ {printer.name}",
                callback_data=f"printer_{printer.id}"
            )
        builder.button(text="⬅️ Назад", callback_data="settings_back")
        builder.adjust(1)
        
        await callback.message.edit_text(
            f"🖨️ <b>{manufacturer}</b>\n\nВыберите вашу модель:",
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )


    @dp.callback_query(F.data.startswith("printer_"))
    async def handle_printer_select(callback: types.CallbackQuery):
        """Handle printer selection"""
        user_id = str(callback.from_user.id)
        printer_id = callback.data.replace("printer_", "")
        
        printer = db.get_printer(printer_id)
        if not printer:
            await callback.answer("❌ Принтер не найден", show_alert=True)
            return
        
        # Update user's printer
        db.update_user_printer(user_id, printer_id)
        
        await callback.answer(f"✅ Выбран: {printer.name}", show_alert=False)
        
        # Update profile message
        user = db.get_user(user_id)
        builder = InlineKeyboardBuilder()
        builder.button(text="⬅️ Назад к настройкам", callback_data="settings_back")
        
        await callback.message.edit_text(
            f"✅ <b>Принтер настроен!</b>\n\n"
            f"🖨️ <b>{printer.name}</b>\n"
            f"• Производитель: {printer.manufacturer}\n"
            f"• Объем печати: {printer.build_volume_x}×{printer.build_volume_y}×{printer.build_volume_z} мм\n"
            f"• Потребление энергии: {printer.power_consumption} кВт/ч\n"
            f"• Диаметр сопла: {printer.nozzle_diameter} мм\n"
            f"• Макс. температура сопла: {printer.max_nozzle_temp}°C\n"
            f"• Макс. температура стола: {printer.max_bed_temp}°C\n\n"
            f"Поддерживаемые филаменты: {', '.join(printer.filament_types)}",
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )


    @dp.callback_query(F.data == "tariffs_menu")
    async def handle_tariffs_menu(callback: types.CallbackQuery):
        """Show tariffs menu"""
        tariffs = db.get_all_tariffs()
        
        builder = InlineKeyboardBuilder()
        for tariff in tariffs:
            builder.button(
                text=f"⚡ {tariff.name} ({tariff.price_per_kwh} {tariff.currency})",
                callback_data=f"tariff_{tariff.id}"
            )
        builder.button(text="➕ Добавить свой тариф", callback_data="add_custom_tariff")
        builder.button(text="⬅️ Назад", callback_data="settings_back")
        builder.adjust(1)
        
        await callback.message.edit_text(
            "⚡ <b>Тарифы на электроэнергию</b>\n\n"
            "Выберите ваш тариф для расчета энергозатрат:",
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )


    @dp.callback_query(F.data.startswith("tariff_"))
    async def handle_tariff_select(callback: types.CallbackQuery):
        """Handle tariff selection"""
        user_id = str(callback.from_user.id)
        tariff_id = callback.data.replace("tariff_", "")
        
        tariff = db.get_tariff(tariff_id)
        if not tariff:
            await callback.answer("❌ Тариф не найден", show_alert=True)
            return
        
        # Update user's tariff
        db.update_user_tariff(user_id, tariff_id)
        
        await callback.answer(f"✅ Выбран тариф: {tariff.name}", show_alert=False)
        
        builder = InlineKeyboardBuilder()
        builder.button(text="⬅️ Назад к тарифам", callback_data="tariffs_menu")
        
        peak_info = ""
        if tariff.peak_hours_start != tariff.peak_hours_end:
            peak_info = f"\n• Пиковые часы: {tariff.peak_hours_start}:00 - {tariff.peak_hours_end}:00 (x{tariff.peak_multiplier})"
        
        await callback.message.edit_text(
            f"✅ <b>Тариф настроен!</b>\n\n"
            f"⚡ <b>{tariff.name}</b>\n"
            f"• Цена: {tariff.price_per_kwh} {tariff.currency}/кВт·ч{peak_info}",
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )


    @dp.callback_query(F.data == "presets_menu")
    async def handle_presets_menu(callback: types.CallbackQuery):
        """Show presets menu"""
        user_id = str(callback.from_user.id)
        presets = db.get_user_presets(user_id)
        
        builder = InlineKeyboardBuilder()
        for preset in presets:
            default_mark = " ⭐" if preset.is_default else ""
            builder.button(
                text=f"💾 {preset.name}{default_mark}",
                callback_data=f"preset_edit_{preset.id}"
            )
        builder.button(text="➕ Создать новый пресет", callback_data="preset_create")
        builder.button(text="⬅️ Назад", callback_data="settings_back")
        builder.adjust(1)
        
        preset_list = "\n".join([f"• {p.name}" for p in presets]) if presets else "Нет сохраненных пресетов"
        
        await callback.message.edit_text(
            f"💾 <b>Пресеты калькулятора</b>\n\n"
            f"Ваши пресеты:\n{preset_list}",
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )


    @dp.callback_query(F.data == "settings_back")
    async def handle_settings_back(callback: types.CallbackQuery):
        """Go back to settings menu"""
        user_id = str(callback.from_user.id)
        user = db.get_user(user_id)
        
        builder = InlineKeyboardBuilder()
        builder.button(text="🖨️ Выбрать принтер", callback_data="printers_list")
        builder.button(text="⚡ Тарифы на энергию", callback_data="tariffs_menu")
        builder.button(text="💾 Пресеты калькулятора", callback_data="presets_menu")
        builder.adjust(1)
        
        current_printer = ""
        if user and user.printer_id:
            printer = db.get_printer(user.printer_id)
            if printer:
                current_printer = f"\n\n✅ Текущий принтер: <b>{printer.name}</b>"
        
        await callback.message.edit_text(
            f"⚙️ <b>Настройки</b>{current_printer}\n\n"
            f"Выберите раздел:",
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )


    @dp.callback_query(F.data == "printers_list")
    async def handle_printers_list(callback: types.CallbackQuery):
        """Show all printers list"""
        printers = db.get_all_printers()
        manufacturers = {}
        for p in printers:
            if p.manufacturer not in manufacturers:
                manufacturers[p.manufacturer] = []
            manufacturers[p.manufacturer].append(p)
        
        builder = InlineKeyboardBuilder()
        for manufacturer in sorted(manufacturers.keys()):
            builder.button(
                text=f"🖨️ {manufacturer}",
                callback_data=f"manuf_{manufacturer}"
            )
        builder.button(text="⬅️ Назад", callback_data="settings_back")
        builder.adjust(1)
        
        await callback.message.edit_text(
            "🖨️ <b>Выберите производителя</b>",
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )


    @dp.callback_query(F.data == "my_presets")
    async def handle_my_presets(callback: types.CallbackQuery):
        """Show user's presets from profile"""
        await handle_presets_menu(callback)


    @dp.callback_query(F.data == "settings_from_profile")
    async def handle_settings_from_profile(callback: types.CallbackQuery):
        """Go to settings from profile"""
        await handle_settings_back(callback)


    @dp.message(F.document)
    async def handle_document(message: types.Message):
        """Handle document (file) uploads"""
        doc = message.document
        
        if not doc.file_name.lower().endswith('.3mf'):
            await message.answer("❌ Пожалуйста, отправьте .3mf файл")
            return
        
        if doc.file_size > 200 * 1024 * 1024:
            await message.answer("❌ Файл слишком большой (макс. 200MB)")
            return
        
        # Download file
        file = await bot.get_file(doc.file_id)
        session_id = uuid.uuid4().hex
        file_path = UPLOAD_FOLDER / f"{session_id}_input.3mf"
        
        await bot.download_file(file.file_path, file_path)
        
        # Validate ZIP
        try:
            with open(file_path, 'rb') as f:
                magic = f.read(4)
            if magic != b'PK\x03\x04':
                file_path.unlink()
                await message.answer("❌ Файл не является корректным 3MF архивом")
                return
        except Exception as e:
            await message.answer(f"❌ Ошибка чтения файла: {e}")
            return
        
        # Parse filaments
        filaments = parse_3mf_filaments(file_path)
        
        if not filaments:
            file_path.unlink()
            await message.answer("❌ Не удалось получить информацию о филаментах из файла")
            return
        
        # Store session
        _sessions[session_id] = {
            'filename': doc.file_name,
            'filaments': filaments,
            'timestamp': time.time(),
            'user_id': message.from_user.id,
            'input_path': file_path,
        }
        
        # Send analysis result
        filament_info = "\n".join([
            f"{i+1}. 🎨 {f.type} ({f.color}) - {f.used_g:.1f}г"
            for i, f in enumerate(filaments)
        ])
        
        await message.answer(
            f"✅ <b>Файл проанализирован!</b>\n\n"
            f"📁 Название: {doc.file_name}\n"
            f"🔢 Филаментов: {len(filaments)}\n\n"
            f"<b>Обнаруженные филаменты:</b>\n"
            f"{filament_info}\n\n"
            f"Теперь выберите действие:",
            parse_mode="HTML",
            reply_markup=get_filament_selection_keyboard(filaments, session_id)
        )


    @dp.callback_query(F.data.startswith("filament_"))
    async def handle_filament_selection(callback: types.CallbackQuery):
        """Handle filament selection callback"""
        parts = callback.data.split("_")
        if len(parts) < 3:
            await callback.answer("❌ Неверный формат", show_alert=True)
            return
        
        session_id = parts[1]
        filament_id = parts[2]
        
        if session_id not in _sessions:
            await callback.answer("❌ Сессия не найдена", show_alert=True)
            return
        
        await callback.answer(f"Филамент {filament_id} выбран")


    @dp.callback_query(F.data.startswith("done_"))
    async def handle_done_selection(callback: types.CallbackQuery):
        """Handle done filament selection"""
        session_id = callback.data.split("_")[1]
        
        if session_id not in _sessions:
            await callback.answer("❌ Сессия не найдена", show_alert=True)
            return
        
        session_data = _sessions[session_id]
        filaments = session_data['filaments']
        input_path = session_data['input_path']
        
        # Prepare default colors mapping
        user_colors = {}
        for fil in filaments[:TARGET_FILAMENTS]:
            user_colors[fil.id] = {
                'color': fil.color,
                'type': fil.type if fil.type in {f['type'] for f in AVAILABLE_FILAMENTS} else 'PLA'
            }
        
        await callback.message.edit_reply_markup(reply_markup=None)
        
        processing_msg = await callback.message.answer("⏳ Конвертирую файл...")
        
        # Perform conversion
        result = convert_3mf_to_u1(input_path, session_id, user_colors, filaments)
        
        await processing_msg.delete()
        
        if not result.success:
            await callback.message.answer(f"❌ Ошибка конвертации: {result.error}")
            return
        
        # Send result
        cost = result.cost_estimate
        cost_text = (
            f"💰 <b>Ориентировочная стоимость:</b>\n"
            f"• База: ${cost['base_price']:.2f}\n"
            f"• Материал: ${cost['material_cost']:.2f} ({cost['total_weight_g']:.1f}г)\n"
            f"• Время: ${cost['time_cost']:.2f}\n"
            f"• Поддержки: {'Да' if cost['has_supports'] else 'Нет'}\n"
            f"<b>Итого: ${cost['total']:.2f}</b>"
        )
        
        # Send file
        input_file = FSInputFile(result.download_path, filename=result.download_name)
        await callback.message.answer_document(
            document=input_file,
            caption=cost_text,
            parse_mode="HTML",
            reply_markup=get_cost_options_keyboard(session_id)
        )
        
        # Store result path in session
        _sessions[session_id]['output_path'] = result.download_path
        _sessions[session_id]['cost_estimate'] = cost


    @dp.callback_query(F.data.startswith("download_"))
    async def handle_download(callback: types.CallbackQuery):
        """Handle download callback"""
        session_id = callback.data.split("_")[1]
        
        if session_id not in _sessions:
            await callback.answer("❌ Сессия не найдена", show_alert=True)
            return
        
        session_data = _sessions[session_id]
        output_path = session_data.get('output_path')
        
        if not output_path or not output_path.exists():
            await callback.answer("❌ Файл не найден", show_alert=True)
            return
        
        input_file = FSInputFile(output_path, filename=session_data.get('filename', 'converted.3mf').replace('.3mf', '-U1.3mf'))
        await callback.message.answer_document(document=input_file)
        await callback.answer()


    @dp.callback_query(F.data.startswith("close_"))
    async def handle_close(callback: types.CallbackQuery):
        """Handle close callback"""
        await callback.message.delete()
        await callback.answer()


    @dp.callback_query(F.data.startswith("cancel_"))
    async def handle_cancel(callback: types.CallbackQuery):
        """Handle cancel callback"""
        session_id = callback.data.split("_")[1]
        _sessions.pop(session_id, None)
        await callback.message.answer("❌ Конвертация отменена")
        await callback.answer()


# ---------------------------------------------------------------------------
# Web server for Mini App
# ---------------------------------------------------------------------------
async def webapp_handler(request):
    """Serve Mini App web interface"""
    html_content = """
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
        <title>3MF Converter Mini App</title>
        <script src="https://telegram.org/js/telegram-web-app.js"></script>
        <script src="https://cdn.tailwindcss.com"></script>
        <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
        <style>
            body { 
                background-color: var(--tg-theme-bg-color, #1e293b);
                color: var(--tg-theme-text-color, #e2e8f0);
            }
        </style>
    </head>
    <body class="font-sans min-h-screen p-4">
        <div class="max-w-lg mx-auto">
            <h1 class="text-2xl font-bold text-center mb-4 text-cyan-400">
                <i class="fa-solid fa-shuttle-space mr-2"></i> 3MF Converter
            </h1>
            
            <div class="bg-slate-800 p-6 rounded-xl shadow-lg border border-slate-700">
                <div id="upload-section">
                    <label class="flex flex-col items-center justify-center w-full h-48 border-2 border-dashed border-slate-600 rounded-lg cursor-pointer hover:border-cyan-500 transition-colors">
                        <div class="text-center">
                            <i class="fa-solid fa-cloud-arrow-up text-4xl mb-3 text-cyan-400"></i>
                            <p class="text-sm">Нажмите для загрузки .3mf файла</p>
                        </div>
                        <input type="file" id="file-input" accept=".3mf" class="hidden" />
                    </label>
                </div>
                
                <div id="loading" class="hidden text-center py-8">
                    <i class="fa-solid fa-circle-notch fa-spin text-4xl text-cyan-400"></i>
                    <p class="mt-4">Обработка...</p>
                </div>
                
                <div id="result" class="hidden mt-6">
                    <div class="bg-slate-700 p-4 rounded-lg mb-4">
                        <h3 class="font-bold mb-2">📊 Результат анализа</h3>
                        <div id="filament-info"></div>
                    </div>
                    
                    <div class="bg-green-900/50 p-4 rounded-lg mb-4 border border-green-600">
                        <h3 class="font-bold mb-2 text-green-400">💰 Стоимость печати</h3>
                        <div id="cost-info"></div>
                    </div>
                    
                    <button id="convert-btn" class="w-full bg-cyan-600 hover:bg-cyan-500 text-white font-bold py-3 px-4 rounded-lg transition-colors">
                        <i class="fa-solid fa-bolt mr-2"></i> Конвертировать и скачать
                    </button>
                </div>
                
                <div id="error" class="hidden mt-4 bg-red-900/50 border border-red-600 text-red-200 px-4 py-3 rounded-lg">
                    <span id="error-message"></span>
                </div>
            </div>
        </div>
        
        <script>
            const tg = window.Telegram.WebApp;
            tg.ready();
            tg.expand();
            
            let sessionId = '';
            
            document.getElementById('file-input').addEventListener('change', async (e) => {
                const file = e.target.files[0];
                if (!file) return;
                
                if (!file.name.toLowerCase().endsWith('.3mf')) {
                    showError('Только .3mf файлы поддерживаются');
                    return;
                }
                
                document.getElementById('upload-section').classList.add('hidden');
                document.getElementById('loading').classList.remove('hidden');
                
                const formData = new FormData();
                formData.append('file', file);
                
                try {
                    const response = await fetch('/api/analyze', {
                        method: 'POST',
                        body: formData
                    });
                    
                    const data = await response.json();
                    
                    if (!response.ok) {
                        throw new Error(data.error || 'Ошибка анализа');
                    }
                    
                    sessionId = data.session_id;
                    
                    // Display filament info
                    const filamentInfo = data.filaments.map((f, i) => 
                        `<div class="flex items-center gap-3 py-2 border-b border-slate-600 last:border-0">
                            <div class="w-6 h-6 rounded-full border" style="background-color: ${f.color}"></div>
                            <span>${i+1}. ${f.type} - ${f.used_g.toFixed(1)}г</span>
                        </div>`
                    ).join('');
                    document.getElementById('filament-info').innerHTML = filamentInfo;
                    
                    // Calculate and display cost
                    const totalWeight = data.filaments.reduce((sum, f) => sum + f.used_g, 0);
                    const materialCost = totalWeight * 0.04;
                    const basePrice = 5.0;
                    const total = basePrice + materialCost;
                    
                    document.getElementById('cost-info').innerHTML = `
                        <div class="space-y-1 text-sm">
                            <div class="flex justify-between"><span>База:</span><span>$${basePrice.toFixed(2)}</span></div>
                            <div class="flex justify-between"><span>Материал:</span><span>$${materialCost.toFixed(2)} (${totalWeight.toFixed(1)}г)</span></div>
                            <div class="flex justify-between font-bold text-green-400"><span>Итого:</span><span>$${total.toFixed(2)}</span></div>
                        </div>
                    `;
                    
                    document.getElementById('loading').classList.add('hidden');
                    document.getElementById('result').classList.remove('hidden');
                    
                } catch (err) {
                    document.getElementById('loading').classList.add('hidden');
                    document.getElementById('upload-section').classList.remove('hidden');
                    showError(err.message);
                }
            });
            
            document.getElementById('convert-btn').addEventListener('click', async () => {
                if (!sessionId) return;
                
                document.getElementById('convert-btn').disabled = true;
                document.getElementById('convert-btn').innerHTML = '<i class="fa-solid fa-circle-notch fa-spin mr-2"></i> Конвертация...';
                
                try {
                    const response = await fetch('/api/convert', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ session_id: sessionId })
                    });
                    
                    const data = await response.json();
                    
                    if (!response.ok) {
                        throw new Error(data.error || 'Ошибка конвертации');
                    }
                    
                    // Trigger download
                    const link = document.createElement('a');
                    link.href = data.download_url;
                    link.download = data.download_name;
                    link.click();
                    
                    tg.showPopup({
                        title: 'Готово!',
                        message: 'Файл успешно конвертирован и загружен.',
                        buttons: [{type: 'ok'}]
                    });
                    
                } catch (err) {
                    showError(err.message);
                    document.getElementById('convert-btn').disabled = false;
                    document.getElementById('convert-btn').innerHTML = '<i class="fa-solid fa-bolt mr-2"></i> Конвертировать и скачать';
                }
            });
            
            function showError(msg) {
                document.getElementById('error-message').textContent = msg;
                document.getElementById('error').classList.remove('hidden');
                setTimeout(() => {
                    document.getElementById('error').classList.add('hidden');
                }, 5000);
            }
        </script>
    </body>
    </html>
    """
    
    return aiohttp_web.Response(text=html_content, content_type='text/html')


async def api_analyze(request):
    """API endpoint for Mini App file analysis"""
    try:
        reader = await request.multipart()
        field = await reader.next()
        
        if not field or field.name != 'file':
            return aiohttp_web.json_response({'error': 'No file provided'}, status=400)
        
        # Save file
        session_id = uuid.uuid4().hex
        file_path = UPLOAD_FOLDER / f"{session_id}_input.3mf"
        
        with open(file_path, 'wb') as f:
            while True:
                chunk = await field.read_chunk()
                if not chunk:
                    break
                f.write(chunk)
        
        # Validate
        with open(file_path, 'rb') as f:
            magic = f.read(4)
        if magic != b'PK\x03\x04':
            file_path.unlink()
            return aiohttp_web.json_response({'error': 'Invalid 3MF file'}, status=400)
        
        # Parse filaments
        filaments = parse_3mf_filaments(file_path)
        
        if not filaments:
            file_path.unlink()
            return aiohttp_web.json_response({'error': 'Could not parse filaments'}, status=400)
        
        # Store session
        _sessions[session_id] = {
            'filaments': filaments,
            'timestamp': time.time(),
            'input_path': file_path,
        }
        
        return aiohttp_web.json_response({
            'session_id': session_id,
            'filaments': [
                {
                    'id': f.id,
                    'color': f.color,
                    'type': f.type,
                    'used_g': f.used_g,
                    'used_m': f.used_m,
                }
                for f in filaments
            ]
        })
        
    except Exception as e:
        logger.error("API analyze error: %s", e)
        return aiohttp_web.json_response({'error': str(e)}, status=500)


async def api_convert(request):
    """API endpoint for Mini App conversion"""
    try:
        data = await request.json()
        session_id = data.get('session_id')
        
        if not session_id or session_id not in _sessions:
            return aiohttp_web.json_response({'error': 'Invalid session'}, status=400)
        
        session_data = _sessions[session_id]
        filaments = session_data['filaments']
        input_path = session_data['input_path']
        
        # Default color mapping
        user_colors = {}
        for fil in filaments[:TARGET_FILAMENTS]:
            user_colors[fil.id] = {
                'color': fil.color,
                'type': fil.type if fil.type in {f['type'] for f in AVAILABLE_FILAMENTS} else 'PLA'
            }
        
        # Convert
        result = convert_3mf_to_u1(input_path, session_id, user_colors, filaments)
        
        if not result.success:
            return aiohttp_web.json_response({'error': result.error}, status=400)
        
        return aiohttp_web.json_response({
            'download_url': f'/api/download/{session_id}',
            'download_name': result.download_name,
            'cost_estimate': result.cost_estimate,
        })
        
    except Exception as e:
        logger.error("API convert error: %s", e)
        return aiohttp_web.json_response({'error': str(e)}, status=500)


async def api_download(request):
    """API endpoint for file download"""
    session_id = request.match_info.get('session_id')
    
    if session_id not in _sessions:
        return aiohttp_web.json_response({'error': 'Session not found'}, status=404)
    
    session_data = _sessions[session_id]
    output_path = session_data.get('output_path')
    
    if not output_path or not output_path.exists():
        return aiohttp_web.json_response({'error': 'File not found'}, status=404)
    
    with open(output_path, 'rb') as f:
        file_data = f.read()
    
    filename = session_data.get('filename', 'converted.3mf').replace('.3mf', '-U1.3mf')
    
    return aiohttp_web.Response(
        body=file_data,
        headers={
            'Content-Disposition': f'attachment; filename="{filename}"',
            'Content-Type': 'application/octet-stream',
        }
    )


async def start_web_server():
    """Start aiohttp web server for Mini App"""
    app = aiohttp_web.Application()
    app.router.add_get('/', webapp_handler)
    app.router.add_post('/api/analyze', api_analyze)
    app.router.add_post('/api/convert', api_convert)
    app.router.add_get('/api/download/{session_id}', api_download)
    
    runner = aiohttp_web.AppRunner(app)
    await runner.setup()
    site = aiohttp_web.TCPSite(runner, API_HOST, API_PORT)
    await site.start()
    
    logger.info(f"Web server started on http://{API_HOST}:{API_PORT}")
    return runner


# Main entry point for running only the web server (Mini App)
async def run_web_server_only():
    """Run only the web server without bot"""
    load_filament_profiles()
    runner = await start_web_server()
    
    # Keep running
    try:
        while True:
            await asyncio.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        await runner.cleanup()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
async def main():
    """Main entry point"""
    # Load filament profiles
    load_filament_profiles()
    
    if not bot or not dp:
        logger.info("Bot not configured. Running web server only for Mini App.")
        await run_web_server_only()
        return
    
    # Start web server
    runner = await start_web_server()
    
    # Start bot polling
    try:
        await dp.start_polling(bot)
    except KeyboardInterrupt:
        pass
    finally:
        # Cleanup on shutdown
        await runner.cleanup()
        await bot.session.close()


if __name__ == '__main__':
    import asyncio
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
