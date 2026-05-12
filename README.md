# Bambu Lab to Snapmaker U1 Converter

A web-based tool and Telegram bot for converting Bambu Lab .3mf projects to Snapmaker U1 format, with print cost calculation.

**Live version:** [https://bl2u1.nbn.cat](https://bl2u1.nbn.cat)

## Features

- Converts Bambu Lab/Bambu Studio .3mf files to Snapmaker U1 compatible format
- Preserves color painting and multi-color assignments
- Applies the 0.20mm Standard print profile for U1
- Remaps filament types to U1 compatible profiles
- Automatically enables Tree Supports (auto) if the original model has supports enabled
- Supports files with more than 4 filaments (select which ones to keep for the U1)
- Real-time upload progress bar
- Downloaded file keeps the original name (e.g. `my_model-U1.3mf`)
- Simple drag & drop interface
- No installation required (web-based)
- **NEW: Telegram Bot** - Convert files directly in Telegram
- **NEW: Print Cost Calculator** - Automatic cost estimation based on material, weight, and time
- **NEW: Mini App** - Full-featured web interface inside Telegram

## Telegram Bot

The project now includes a Telegram bot that provides:

### Features

📤 **File Conversion**: Upload and convert .3mf files directly in Telegram
💰 **Cost Calculation**: Automatic print cost estimation based on:
   - Material weight
   - Filament type (PLA, PETG, ABS, TPU)
   - Print time
   - Support structures
📱 **Mini App**: Full web interface inside Telegram
🔧 **Filament Configuration**: Select and configure colors and material types

### Quick Start

1. Get a bot token from [@BotFather](https://t.me/BotFather)
2. Copy `.env.example` to `.env` and add your token
3. Install dependencies: `pip install -r requirements.txt`
4. Run: `cd telegram_bot && python bot.py`

See [telegram_bot/README.md](telegram_bot/README.md) for detailed instructions.

## How It Works

1. Upload your Bambu Lab .3mf file
2. Review and adjust filament colors/types if needed
3. Click "Convert and Download"
4. Open the converted file in **Snapmaker Orca** for final slicing

## Self-Hosting

### Requirements

- Python 3.9+
- Flask (for web app)
- aiogram 3.x (for Telegram bot)
- aiohttp 3.9+ (for Mini App server)

### Installation

```bash
# Clone the repository
git clone https://github.com/josuanbn/bl2u1.git
cd bl2u1

# Install dependencies
pip install -r requirements.txt

# Run the web application
python app.py

# Or run the Telegram bot + Mini App
cd telegram_bot
python bot.py
```

The web application will be available at `http://localhost:8080`

### Project Structure

```
bl2u1/
├── app.py                    # Flask backend (web app)
├── templates/
│   └── index.html            # Frontend interface
├── uploads/                  # Temporary file storage (auto-cleaned)
├── u1_template.3mf           # U1 template without supports
├── u1_template_supports.3mf  # U1 template with tree supports
├── filament_types.3mf        # Available filament profiles
└── telegram_bot/             # Telegram bot + Mini App
    ├── bot.py                # Bot and Mini App server
    ├── README.md             # Bot documentation
    └── .env.example          # Environment variables template
```

### Template Files

The converter requires template .3mf files configured for Snapmaker U1:

- `u1_template.3mf` - Base template with 0.20mm Standard profile, supports disabled
- `u1_template_supports.3mf` - Same as above but with Tree Supports (auto) enabled
- `filament_types.3mf` - Reference file containing available U1 filament profiles

## Technical Details

The converter performs the following transformations:

1. **Printer Profile**: Changes printer settings from Bambu Lab to Snapmaker U1
2. **Filament Mapping**: Remaps filament types to U1 compatible profiles
3. **Color Preservation**: Maintains all color painting data from the original file
4. **Support Detection**: Checks `different_settings_to_system` for `enable_support` and uses the appropriate template
5. **Filament Padding**: Ensures 4 filaments are always configured (fills empty slots with white PLA)
6. **Cost Calculation**: Estimates print cost based on material type, weight, and print time

### File Cleanup

Uploaded files are automatically deleted after 8 hours to save disk space.

## Pricing Configuration

The cost calculator uses the following default pricing (configurable in `telegram_bot/bot.py`):

```python
PRICING_CONFIG = {
    "base_price": 5.0,        # Base setup fee ($)
    "price_per_gram": 0.05,   # Price per gram of filament
    "price_per_hour": 2.0,    # Price per hour of printing
    "support_multiplier": 1.2,# Multiplier if supports are needed
    "material_prices": {
        "PLA": 0.03,
        "PETG": 0.04,
        "ABS": 0.05,
        "TPU": 0.06,
    }
}
```

## Limitations

- Maximum 4 filaments/colors per print (U1 hardware limitation), but files with more filaments are supported via selection
- The converted file must be sliced in Snapmaker Orca before printing
- Some advanced Bambu-specific features may not transfer

## Contributing

Contributions are welcome! Feel free to:

- Report bugs
- Suggest features
- Submit pull requests

## License

MIT License - feel free to use, modify, and distribute.

## Acknowledgments

- Snapmaker community for feedback and testing
- Bambu Lab for the excellent .3mf format documentation

## Support

If you find this tool useful, consider [buying me a coffee](https://buymeacoffee.com/josuanbn)!
