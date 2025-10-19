# Marbles on Stream - Position-Based Bounty Bot

This is a Discord bot designed to automatically track a persistent "bounty" leaderboard for the game **Marbles on Stream**.

Users can submit end-of-race screenshots (even multiple for large games), and the bot uses Optical Character Recognition (OCR) to read the player names and their positions. It then calculates a bounty score for each player based on their placement and updates a server-wide leaderboard.



## ✨ Features

* **Screenshot Processing:** Uses EasyOCR (recommended) or Tesseract to read player names and rankings directly from game screenshots.
* **Multi-Screenshot Merging:** Automatically detects overlapping players and merges multiple screenshots into one continuous ranking for large games.
* **Position-Based Scoring:** Calculates a "bounty" for each race.
    * Top players gain points.
    * Middle-of-the-pack players gain/lose very few points.
    * Bottom players lose points.
    * A significant bonus is given for 1st place.
* **Persistent Leaderboard:** All bounties are saved in a `bounty_board.json` file to track scores over time.
* **Slash Commands:** Easy-to-use `/` commands for submitting results, viewing the leaderboard, and admin controls.
* **Admin Tools:** Includes commands for admins to remove incorrect player entries, edit the last game's results, or reset the leaderboard entirely.
* **Fuzzy Matching:** Tolerant of common OCR errors (e.g., "Pl" -> "P1", "S" -> "5") to improve parsing accuracy.

## 🔧 Setup and Installation

### 1. Project Setup

1.  Clone this repository or download the Python script.
2.  Open a terminal in the project directory and install the required Python dependencies:

    ```bash
    pip install discord.py pillow easyocr aiohttp numpy
    ```

3.  **(Optional) Tesseract OCR Setup:**
    If you prefer to use Tesseract instead of EasyOCR, you must install the Tesseract binary *in addition* to the Python library.

    * **Windows:**
        1.  Download the installer from [UB-Mannheim/tesseract/wiki](https://github.com/UB-Mannheim/tesseract/wiki).
        2.  Install it (e.g., to `C:\Program Files\Tesseract-OCR\`).
        3.  Add this installation directory to your system's `PATH` environment variable.
        4.  Run `pip install pytesseract`.
    * **macOS:**
        1.  Run `brew install tesseract`.
        2.  Run `pip install pytesseract`.
    * **Linux (Debian/Ubuntu):**
        1.  Run `sudo apt-get install tesseract-ocr`.
        2.  Run `pip install pytesseract`.

### 2. Discord Bot Setup

1.  Go to the [Discord Developer Portal](https://discord.com/developers/applications) and create a **New Application**.
2.  Go to the **"Bot"** tab.
3.  Under **"Privileged Gateway Intents"**, enable the **MESSAGE CONTENT INTENT**. This is required for the bot to read messages.
4.  Copy your bot's **Token** (you may need to click "Reset Token"). This is your `BOT_TOKEN`.
5.  Invite the bot to your server using this URL. Replace `YOUR_CLIENT_ID` with your bot's **Application ID** (from the "General Information" page).

    ```
    [https://discord.com/api/oauth2/authorize?client_id=YOUR_CLIENT_ID&permissions=274878024768&scope=bot%20applications.commands](https://discord.com/api/oauth2/authorize?client_id=YOUR_CLIENT_ID&permissions=274878024768&scope=bot%20applications.commands)
    ```

### 3. Configuration

Open the Python script and edit the **CONFIGURATION** section at the top:

* `BOT_TOKEN`: Paste your bot token here.
    ```python
    BOT_TOKEN = 'YOUR_BOT_TOKEN_HERE'
    ```
* `GUILD_ID`: (Highly Recommended) Find your server's ID (right-click server icon -> "Copy Server ID") and replace the placeholder. This makes slash commands update instantly on your server.
    ```python
    GUILD_ID = discord.Object(id=123456789012345678) # Replace with your server's ID
    ```
* `OCR_ENGINE`: Change to `'tesseract'` if you installed and prefer Tesseract.
    ```python
    OCR_ENGINE = 'easyocr' # or 'tesseract'
    ```
* **Scoring (Optional):** You can tweak the bounty calculation:
    * `WIN_BONUS = 200`: The bonus points awarded for 1st place.
    * `PLACEMENT_FACTOR = 20`: The multiplier for placement score.

## 🚀 How to Run

Once configured, simply run the bot from your terminal:

```bash
python your_bot_script_name.py
