"""
MARBLES ON STREAM DISCORD BOT - POSITION-BASED RANKING
======================================================

DEPENDENCIES INSTALLATION:
--------------------------
pip install discord.py pillow easyocr aiohttp numpy

For Tesseract OCR (alternative to EasyOCR):
- Windows: Download installer from https://github.com/UB-Mannheim/tesseract/wiki
  Install to C:\\Program Files\\Tesseract-OCR\\ and add to PATH
  Then: pip install pytesseract

- Mac: brew install tesseract
  Then: pip install pytesseract

- Linux: sudo apt-get install tesseract-ocr
  Then: pip install pytesseract

SETUP:
------
1. Create a Discord bot at https://discord.com/developers/applications
2. Enable "Message Content Intent" in Bot settings (for text commands)
3. IMPORTANT: Invite bot with this URL (replace CLIENT_ID with your Application ID):
   https://discord.com/api/oauth2/authorize?client_id=1423680828734705716&permi ssions=274878024768&scope=bot%20applications.commands
4. Replace 'YOUR_BOT_TOKEN_HERE' below with your actual bot token
5. Replace 'YOUR_GUILD_ID_HERE' with your server ID for faster command sync (optional)

CONFIGURATION:
--------------
"""

from discord.ui import View, Button, Select
import discord
from discord import app_commands
from discord.ext import commands
import re
import json
import os
from typing import Dict, List, Optional, Tuple
import aiohttp
from io import BytesIO
from PIL import Image
import numpy as np

# Choose OCR engine: 'easyocr' (recommended) or 'tesseract'
OCR_ENGINE = 'easyocr'

if OCR_ENGINE == 'easyocr':
    import easyocr
    # Initialize EasyOCR reader (will download model on first run)
    reader = easyocr.Reader(['en'], gpu=False)
elif OCR_ENGINE == 'tesseract':
    import pytesseract
    # Uncomment and set path if Tesseract not in PATH (Windows example):
    # pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

# =============================================================================
# CONFIGURATION
# =============================================================================

BOT_TOKEN = 'BOT_TOKEN'
GUILD_ID = discord.Object(id=1423712633919377553 )  # Replace with: discord.Object(id=1234567890)

# Bounty scoring constants
WIN_BONUS = 200
PLACEMENT_FACTOR = 20

# Database file
DB_FILE = 'bounty_board.json'

# =============================================================================
# BOT SETUP
# =============================================================================

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents)

# =============================================================================
# OCR ERROR CORRECTION FUNCTIONS
# =============================================================================

def normalize_ocr_text(text: str) -> str:
    """
    Normalize text to handle common OCR errors.
    Converts ambiguous characters to a standard form for comparison.
    
    Common OCR errors handled:
    - i/I/l/1 (all become 'i')
    - o/O/0 (all become 'o')
    - s/S/5 (all become 's')
    
    Args:
        text: Raw OCR text
    
    Returns:
        Normalized text for fuzzy matching
    """
    text = text.lower()
    
    # Replace common OCR misreads
    text = re.sub(r'[l1]', 'i', text)
    text = re.sub(r'0', 'o', text)
    text = re.sub(r'5', 's', text)
    
    return text


def fuzzy_match_keyword(text: str, keyword: str) -> bool:
    """
    Check if a keyword appears in text, accounting for OCR errors.
    
    Args:
        text: The text to search in (e.g., "Tlme" or "Time")
        keyword: The keyword to find (e.g., "time")
    
    Returns:
        True if keyword found (with OCR tolerance)
    """
    normalized_text = normalize_ocr_text(text)
    normalized_keyword = normalize_ocr_text(keyword)
    
    return normalized_keyword in normalized_text


def fuzzy_match_any(text: str, keywords: list) -> bool:
    """
    Check if any keyword from a list appears in text.
    
    Args:
        text: The text to search in
        keywords: List of keywords to search for
    
    Returns:
        True if any keyword is found
    """
    return any(fuzzy_match_keyword(text, kw) for kw in keywords)


# =============================================================================
# SCREENSHOT MERGING FUNCTIONS
# =============================================================================

def merge_screenshot_data(parsed_data_list: List[Dict]) -> Dict:
    """
    Merge multiple parsed screenshots into one continuous ranking.
    
    Handles overlapping players between screenshots by detecting duplicates
    and continuing position numbering from where the previous screenshot ended.
    """
    
    if len(parsed_data_list) == 1:
        return parsed_data_list[0]
    
    print("\n=== MERGING SCREENSHOTS ===")
    
    # Start with first screenshot
    merged_results = list(parsed_data_list[0]['results'])  # [(name, position), ...]
    current_max_position = len(merged_results)
    
    print(f"Screenshot 1: {len(merged_results)} players (positions 1-{current_max_position})")
    
    # Process subsequent screenshots
    for idx, parsed_data in enumerate(parsed_data_list[1:], 2):
        new_results = parsed_data['results']
        
        print(f"\nScreenshot {idx}: {len(new_results)} players")
        
        # Find overlapping players (players that appear in both)
        existing_names = {name.lower() for name, _ in merged_results}
        overlap_count = 0
        new_players_added = 0
        
        for player_name, original_position in new_results:
            player_lower = player_name.lower()
            
            if player_lower in existing_names:
                # This is an overlapping player - skip
                overlap_count += 1
                print(f"   Overlap: {player_name} (already in results)")
            else:
                # New player - add with next position
                current_max_position += 1
                merged_results.append((player_name, current_max_position))
                existing_names.add(player_lower)
                new_players_added += 1
                print(f"   Added: {player_name} at position {current_max_position}")
        
        print(f"Screenshot {idx} summary: {overlap_count} overlaps, {new_players_added} new players added")
    
    print(f"\n✅ Merge complete: {len(merged_results)} total unique players")
    
    return {
        'total_players': len(merged_results),
        'results': merged_results
    }

# =============================================================================
# DATABASE FUNCTIONS
# =============================================================================

def load_bounty_board() -> Dict[str, int]:
    """Load bounty board from JSON file"""
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError:
            print(f"Warning: Could not read {DB_FILE}, starting fresh")
    return {}

def save_bounty_board(board: Dict[str, int]):
    """Save bounty board to JSON file"""
    with open(DB_FILE, 'w') as f:
        json.dump(board, f, indent=2)

# Initialize bounty board
bounty_board = load_bounty_board()

# Store last game data for editing
last_game_data = None

# =============================================================================
# OCR FUNCTIONS
# =============================================================================

async def download_image_from_attachment(attachment: discord.Attachment) -> Optional[Image.Image]:
    """Download image from Discord attachment and return PIL Image"""
    try:
        data = await attachment.read()
        return Image.open(BytesIO(data))
    except Exception as e:
        print(f"Error downloading image: {e}")
    return None

def perform_ocr(image: Image.Image) -> str:
    """Perform OCR on image and return extracted text"""
    try:
        if OCR_ENGINE == 'easyocr':
            # Convert PIL Image to numpy array for EasyOCR
            image_array = np.array(image)
            # EasyOCR returns list of (bbox, text, confidence)
            results = reader.readtext(image_array)
            text = '\n'.join([result[1] for result in results])
        else:  # tesseract
            text = pytesseract.image_to_string(image)
        return text
    except Exception as e:
        print(f"OCR Error: {e}")
        return ""

# =============================================================================
# PARSING FUNCTIONS
# =============================================================================

def clean_player_name(name: str) -> str:
    """Clean and normalize player names"""
    # Remove special characters, extra spaces
    name = re.sub(r'[^\w\s-]', '', name)
    name = ' '.join(name.split())
    return name.strip()

def parse_marbles_screenshot(text: str) -> Optional[Dict[str, any]]:
    """
    Parse OCR text to extract player data from Marbles on Stream.
    Position-based ranking only.
    """
    lines = text.split('\n')
    results = []

    # Clean and filter lines
    clean_lines = []
    for line in lines:
        line = line.strip()
        if line and len(line) > 2:
            clean_lines.append(line)

    print(f"Processing {len(clean_lines)} lines")
    print("Raw OCR text:")
    for i, line in enumerate(clean_lines[:50]):  # print first 50 lines for debugging
        print(f"   {i}: {line}")

    # Parse each line looking for player data
    position = 0
    header_found = False

    # Header keywords to skip
    header_keywords = ['place', 'player', 'time', 'points', 'damage',
                       'wins', 'races', 'elimination', 'name']

    # Specific words to ignore (OCR artifacts/misreads)
    ignore_words = ['even', 'dltc', 'def', 'juaz','dne']

    for line_idx, line in enumerate(clean_lines):
        # Detect header line using fuzzy matching
        if fuzzy_match_any(line, ['place', 'player']):
            header_found = True
            print(f"Header detected at line {line_idx}: {line}")
            continue

        # Skip lines that are purely header remnants
        if fuzzy_match_any(line, header_keywords) and not any(c.isdigit() for c in line):
            print(f"Skipping header remnant: {line}")
            continue

        tokens = line.split()
        player_name = None

        # Find the player name
        for i, token in enumerate(tokens):
            token_clean = re.sub(r'[^\w_]', '', token)

            # Skip placement indicators
            if token.upper() in ['1ST', '2ND', '3RD', 'DNF', 'IST']:
                continue

            # Skip if it matches common header words
            if fuzzy_match_any(token_clean, header_keywords):
                continue

            # Skip specific ignore words
            if token_clean.lower() in ignore_words:
                print(f"Skipping ignore word: {token_clean}")
                continue

            # Check if this looks like a username
            if re.match(r'^[A-Za-z][A-Za-z0-9_]*$', token_clean) and len(token_clean) >= 3:
                player_name = clean_player_name(token_clean)
                break

        # If we found a player name, add them
        if player_name and len(player_name) >= 3:
            if not any(p[0].lower() == player_name.lower() for p in results):
                position += 1
                results.append((player_name, position))
                print(f"✓ Position {position}: {player_name}")

    # Fallback: aggressive parsing if we found very few results
    if len(results) < 2:
        print("Standard parsing found few results, trying aggressive mode...")
        results = []
        position = 0

        for line in clean_lines:
            if fuzzy_match_any(line, ['place', 'player']):
                continue

            potential_names = re.findall(r'[A-Za-z][A-Za-z0-9_]{2,}', line)

            for name in potential_names:
                if fuzzy_match_any(name, header_keywords):
                    continue

                # Skip ignore words in aggressive mode too
                if name.lower() in ignore_words:
                    continue

                if any(p[0].lower() == name.lower() for p in results):
                    continue

                position += 1
                results.append((name, position))
                print(f"✓ (Aggressive) Position {position}: {name}")
                break

    if not results:
        print("❌ No results found!")
        return None

    print(f"\n✅ Successfully parsed {len(results)} players")
    return {
        'total_players': len(results),
        'results': results
    }

# =============================================================================
# BOUNTY CALCULATION
# =============================================================================

def calculate_bounty(position: int, total_players: int, is_winner: bool = False) -> int:
    """
    Calculate bounty based on placement only.
    
    Formula:
    - Win (1st place): +200 bonus
    - Placement: ((N - position + 1) - (N/2)) * 20
        * First place: highest positive score
        * Middle positions: ~0 points
        * Last place: highest negative score
    """
    
    # Win bonus
    win_bounty = WIN_BONUS if is_winner else 0
    
    # Placement score: ((N - position + 1) - (N/2)) * factor
    placement_score = ((total_players - position + 1) - (total_players / 2)) * PLACEMENT_FACTOR
    
    # Total bounty
    total_bounty = win_bounty + int(placement_score)
    
    return total_bounty

def update_bounty_board(parsed_data: Dict):
    """Update global bounty board with new results"""
    global last_game_data
    
    # Store for potential editing
    last_game_data = parsed_data.copy()
    
    total_players = parsed_data['total_players']
    
    for player_name, position in parsed_data['results']:
        is_winner = (position == 1)
        bounty = calculate_bounty(position, total_players, is_winner)
        
        # Add to player's total bounty
        if player_name in bounty_board:
            bounty_board[player_name] += bounty
        else:
            bounty_board[player_name] = bounty
    
    save_bounty_board(bounty_board)

# =============================================================================
# PLAYER EDITING VIEW (INTERACTIVE BUTTONS)
# =============================================================================

class LeaderboardEditView(View):
    """Interactive view for editing the leaderboard - remove incorrect players"""
    
    def __init__(self, removed_players: List[str] = None, page: int = 0):
        super().__init__(timeout=300)  # 5 minute timeout
        self.removed_players = removed_players or []
        self.page = page
        self.players_per_page = 25
        self.update_view()
    
    def get_sorted_leaderboard(self):
        """Get current leaderboard sorted by bounty"""
        return sorted(bounty_board.items(), key=lambda x: x[1], reverse=True)
    
    def get_available_players(self):
        """Get players not yet marked for removal"""
        all_players = self.get_sorted_leaderboard()
        return [(name, bounty) for name, bounty in all_players if name not in self.removed_players]
    
    def get_total_pages(self):
        """Calculate total pages needed"""
        available = self.get_available_players()
        return max(1, (len(available) + self.players_per_page - 1) // self.players_per_page)
    
    def update_view(self):
        """Update the view with pagination"""
        self.clear_items()
        
        available_players = self.get_available_players()
        total_pages = self.get_total_pages()
        
        if not available_players:
            return
        
        # Calculate pagination
        start_idx = self.page * self.players_per_page
        end_idx = start_idx + self.players_per_page
        page_players = available_players[start_idx:end_idx]
        
        # Create select menu for current page
        options = []
        for rank, (name, bounty) in enumerate(page_players, start=start_idx + 1):
            options.append(
                discord.SelectOption(
                    label=f"#{rank} {name} ({bounty:+})"[:100],
                    value=name,
                    description=f"Remove this player from leaderboard"
                )
            )
        
        if options:
            select = Select(
                placeholder=f"Page {self.page + 1}/{total_pages} - Select player(s) to remove...",
                options=options,
                min_values=1,
                max_values=len(options)
            )
            select.callback = self.player_selected
            self.add_item(select)
        
        # Navigation buttons (in a row)
        if self.page > 0:
            prev_button = Button(label="◀ Previous", style=discord.ButtonStyle.secondary, row=1)
            prev_button.callback = self.previous_page
            self.add_item(prev_button)
        
        if self.page < total_pages - 1:
            next_button = Button(label="Next ▶", style=discord.ButtonStyle.secondary, row=1)
            next_button.callback = self.next_page
            self.add_item(next_button)
        
        # Action buttons (in a new row)
        done_button = Button(label="✅ Done - Remove Selected", style=discord.ButtonStyle.success, row=2)
        done_button.callback = self.done_editing
        self.add_item(done_button)
        
        cancel_button = Button(label="❌ Cancel", style=discord.ButtonStyle.danger, row=2)
        cancel_button.callback = self.cancel_editing
        self.add_item(cancel_button)
    
    async def previous_page(self, interaction: discord.Interaction):
        """Go to previous page"""
        self.page = max(0, self.page - 1)
        self.update_view()
        await self.update_message(interaction)
    
    async def next_page(self, interaction: discord.Interaction):
        """Go to next page"""
        total_pages = self.get_total_pages()
        self.page = min(total_pages - 1, self.page + 1)
        self.update_view()
        await self.update_message(interaction)
    
    async def update_message(self, interaction: discord.Interaction):
        """Update the message content"""
        available_players = self.get_available_players()
        total_players = len(self.get_sorted_leaderboard())
        total_pages = self.get_total_pages()
        
        start_idx = self.page * self.players_per_page
        end_idx = start_idx + self.players_per_page
        page_players = available_players[start_idx:end_idx]
        
        player_list = "\n".join([
            f"#{rank} {name} ({bounty:+})" 
            for rank, (name, bounty) in enumerate(page_players, start=start_idx + 1)
        ])
        
        content = f"**🛠️ Edit Leaderboard**\n\n"
        content += f"Total Players: {total_players} | Available: {len(available_players)}\n"
        content += f"Page {self.page + 1}/{total_pages}\n\n"
        
        if self.removed_players:
            removed_list = ", ".join(self.removed_players[:10])
            if len(self.removed_players) > 10:
                removed_list += f" +{len(self.removed_players) - 10} more"
            content += f"**Marked for Removal ({len(self.removed_players)}):** {removed_list}\n\n"
        
        content += f"**Players on this page:**\n{player_list}\n\n"
        content += "Select players to remove, navigate pages, or click Done:"
        
        await interaction.response.edit_message(content=content, view=self)
    
    async def player_selected(self, interaction: discord.Interaction):
        """Handle player removal selection"""
        selected_players = interaction.data['values']
        
        for player in selected_players:
            if player not in self.removed_players:
                self.removed_players.append(player)
        
        # Recalculate pagination (players might be removed from current page)
        total_pages = self.get_total_pages()
        
        # Adjust page if current page is now empty
        if self.page >= total_pages and total_pages > 0:
            self.page = total_pages - 1
        
        self.update_view()
        await self.update_message(interaction)
    
    async def done_editing(self, interaction: discord.Interaction):
        """Remove selected players from leaderboard"""
        if not self.removed_players:
            await interaction.response.edit_message(
                content="❌ No players were selected for removal. Use `/edit_leaderboard` to try again.",
                view=None
            )
            return
        
        await interaction.response.defer()
        
        # Remove players from bounty board
        global bounty_board
        removed_with_bounties = []
        
        for player in self.removed_players:
            if player in bounty_board:
                bounty = bounty_board[player]
                removed_with_bounties.append(f"{player} ({bounty:+})")
                del bounty_board[player]
        
        save_bounty_board(bounty_board)
        
        # Send confirmation
        removed_list = "\n".join(removed_with_bounties[:20])
        if len(removed_with_bounties) > 20:
            removed_list += f"\n... and {len(removed_with_bounties) - 20} more"
        
        await interaction.followup.send(
            f"✅ **Leaderboard Updated!**\n\n**Removed {len(self.removed_players)} player(s):**\n{removed_list}"
        )
        
        # Show updated leaderboard
        leaderboard_messages = format_leaderboard()
        for leaderboard_msg in leaderboard_messages:
            await interaction.followup.send(leaderboard_msg)
        
        # Clear the original message
        await interaction.message.edit(
            content=f"✅ Leaderboard updated! Removed {len(self.removed_players)} player(s).",
            view=None
        )
    
    async def cancel_editing(self, interaction: discord.Interaction):
        """Cancel the editing process"""
        await interaction.response.edit_message(
            content="❌ Editing cancelled. Leaderboard remains unchanged.",
            view=None
        )


class PlayerRemovalView(View):
    """Interactive view for removing incorrect players from last game with pagination"""
    
    def __init__(self, game_data: Dict, removed_players: List[str] = None, page: int = 0):
        super().__init__(timeout=300)  # 5 minute timeout
        self.game_data = game_data
        self.removed_players = removed_players or []
        self.page = page
        self.players_per_page = 25
        self.update_view()
    
    def get_available_players(self):
        """Get players not yet removed"""
        return [
            (name, pos) for name, pos in self.game_data['results'] 
            if name not in self.removed_players
        ]
    
    def get_total_pages(self):
        """Calculate total pages needed"""
        available = self.get_available_players()
        return (len(available) + self.players_per_page - 1) // self.players_per_page
    
    def update_view(self):
        """Update the view with pagination"""
        self.clear_items()
        
        available_players = self.get_available_players()
        total_pages = self.get_total_pages()
        
        if not available_players:
            return
        
        # Calculate pagination
        start_idx = self.page * self.players_per_page
        end_idx = start_idx + self.players_per_page
        page_players = available_players[start_idx:end_idx]
        
        # Create select menu for current page
        options = []
        for name, pos in page_players:
            options.append(
                discord.SelectOption(
                    label=f"#{pos} - {name}"[:100],
                    value=name,
                    description=f"Remove this player"
                )
            )
        
        if options:
            select = Select(
                placeholder=f"Page {self.page + 1}/{total_pages} - Select player(s) to remove...",
                options=options,
                min_values=1,
                max_values=len(options)
            )
            select.callback = self.player_selected
            self.add_item(select)
        
        # Navigation buttons (in a row)
        if self.page > 0:
            prev_button = Button(label="◀ Previous", style=discord.ButtonStyle.secondary, row=1)
            prev_button.callback = self.previous_page
            self.add_item(prev_button)
        
        if self.page < total_pages - 1:
            next_button = Button(label="Next ▶", style=discord.ButtonStyle.secondary, row=1)
            next_button.callback = self.next_page
            self.add_item(next_button)
        
        # Action buttons (in a new row)
        done_button = Button(label="✅ Done - Recalculate Bounties", style=discord.ButtonStyle.success, row=2)
        done_button.callback = self.done_editing
        self.add_item(done_button)
        
        cancel_button = Button(label="❌ Cancel", style=discord.ButtonStyle.danger, row=2)
        cancel_button.callback = self.cancel_editing
        self.add_item(cancel_button)
    
    async def previous_page(self, interaction: discord.Interaction):
        """Go to previous page"""
        self.page = max(0, self.page - 1)
        self.update_view()
        await self.update_message(interaction)
    
    async def next_page(self, interaction: discord.Interaction):
        """Go to next page"""
        total_pages = self.get_total_pages()
        self.page = min(total_pages - 1, self.page + 1)
        self.update_view()
        await self.update_message(interaction)
    
    async def update_message(self, interaction: discord.Interaction):
        """Update the message content"""
        available_players = self.get_available_players()
        total_pages = self.get_total_pages()
        
        start_idx = self.page * self.players_per_page
        end_idx = start_idx + self.players_per_page
        page_players = available_players[start_idx:end_idx]
        
        player_list = "\n".join([f"#{pos} - {name}" for name, pos in page_players])
        
        content = f"**🛠️ Edit Last Game Results**\n\n"
        content += f"Total Players: {self.game_data['total_players']} | Available: {len(available_players)}\n"
        content += f"Page {self.page + 1}/{total_pages}\n\n"
        
        if self.removed_players:
            removed_list = ", ".join(self.removed_players[:10])
            if len(self.removed_players) > 10:
                removed_list += f" +{len(self.removed_players) - 10} more"
            content += f"**Removed ({len(self.removed_players)}):** {removed_list}\n\n"
        
        content += f"**Players on this page:**\n{player_list}\n\n"
        content += "Select players to remove, navigate pages, or click Done:"
        
        await interaction.response.edit_message(content=content, view=self)
    
    async def player_selected(self, interaction: discord.Interaction):
        """Handle player removal selection"""
        selected_players = interaction.data['values']
        
        for player in selected_players:
            if player not in self.removed_players:
                self.removed_players.append(player)
        
        # Recalculate pagination (players might be removed from current page)
        available_players = self.get_available_players()
        total_pages = self.get_total_pages()
        
        # Adjust page if current page is now empty
        if self.page >= total_pages and total_pages > 0:
            self.page = total_pages - 1
        
        self.update_view()
        await self.update_message(interaction)
    
    async def done_editing(self, interaction: discord.Interaction):
        """Recalculate bounties after removing players"""
        if not self.removed_players:
            await interaction.response.edit_message(
                content="❌ No players were removed. Use `/edit_last_game` to try again.",
                view=None
            )
            return
        
        await interaction.response.defer()
        
        # Revert the bounties from the original game
        global bounty_board
        total_players = self.game_data['total_players']
        
        for player_name, position in self.game_data['results']:
            is_winner = (position == 1)
            bounty = calculate_bounty(position, total_players, is_winner)
            
            if player_name in bounty_board:
                bounty_board[player_name] -= bounty
                if bounty_board[player_name] == 0:
                    del bounty_board[player_name]
        
        # Create new results without removed players
        filtered_results = [
            (name, pos) for name, pos in self.game_data['results']
            if name not in self.removed_players
        ]
        
        # Recalculate positions (1, 2, 3, ... without gaps)
        new_results = [(name, idx + 1) for idx, (name, _) in enumerate(filtered_results)]
        
        # Create new parsed data
        new_game_data = {
            'total_players': len(new_results),
            'results': new_results
        }
        
        # Update bounty board with corrected data
        update_bounty_board(new_game_data)
        
        # Send updated results
        removed_list = ", ".join(self.removed_players[:20])
        if len(self.removed_players) > 20:
            removed_list += f" +{len(self.removed_players) - 20} more"
        
        await interaction.followup.send(
            f"✅ **Game Results Updated!**\n\n**Removed {len(self.removed_players)} players:** {removed_list}\n\n**Recalculating bounties...**"
        )
        
        game_results_messages = format_game_results(new_game_data)
        for result_msg in game_results_messages:
            await interaction.followup.send(result_msg)
        
        leaderboard_messages = format_leaderboard()
        for leaderboard_msg in leaderboard_messages:
            await interaction.followup.send(leaderboard_msg)
        
        # Clear the original message
        await interaction.message.edit(
            content=f"✅ Game results updated! Removed {len(self.removed_players)} player(s).",
            view=None
        )
    
    async def cancel_editing(self, interaction: discord.Interaction):
        """Cancel the editing process"""
        await interaction.response.edit_message(
            content="❌ Editing cancelled. Original results remain unchanged.",
            view=None
        )

# =============================================================================
# DISPLAY FUNCTIONS
# =============================================================================

def format_leaderboard() -> List[str]:
    """Format bounty board as a nice leaderboard string, split into multiple messages if needed"""
    if not bounty_board:
        return ["🏆 **BOUNTY LEADERBOARD** 🏆\n\n*No bounties recorded yet!*"]
    
    # Sort by bounty (descending)
    sorted_players = sorted(bounty_board.items(), key=lambda x: x[1], reverse=True)
    
    messages = []
    current_message = "🏆 **BOUNTY LEADERBOARD** 🏆\n\n"
    current_message += "```\n"
    current_message += f"{'Rank':<6} {'Player':<20} {'Bounty':>10}\n"
    current_message += "─" * 40 + "\n"
    
    for rank, (player, bounty) in enumerate(sorted_players, 1):
        medal = "🥇" if rank == 1 else "🥈" if rank == 2 else "🥉" if rank == 3 else "   "
        line = f"{medal} {rank:<3} {player:<20} {bounty:>10}\n"
        
        # Check if adding this line would exceed Discord's limit
        if len(current_message) + len(line) + 10 > 1900:
            # Close current message and start a new one
            current_message += "```"
            messages.append(current_message)
            
            # Start new message
            current_message = "🏆 **BOUNTY LEADERBOARD (continued)** 🏆\n\n"
            current_message += "```\n"
            current_message += f"{'Rank':<6} {'Player':<20} {'Bounty':>10}\n"
            current_message += "─" * 40 + "\n"
        
        current_message += line
    
    # Close final message
    current_message += "```"
    messages.append(current_message)
    
    return messages

def format_game_results(parsed_data: Dict) -> List[str]:
    """Format individual game results, split into multiple messages if needed"""
    total_players = parsed_data['total_players']
    results = parsed_data['results']
    
    messages = []
    current_message = f"🏁 **RACE RESULTS** (Total Players: {total_players})\n\n"
    current_message += "```\n"
    current_message += f"{'Pos':<5} {'Player':<20} {'Bounty':>10}\n"
    current_message += "─" * 40 + "\n"
    
    for player_name, position in results:
        is_winner = (position == 1)
        bounty = calculate_bounty(position, total_players, is_winner)
        medal = "👑" if is_winner else "   "
        
        line = f"{medal}{position:<4} {player_name:<20} {bounty:>+10}\n"
        
        # Check if adding this line would exceed Discord's limit (leaving room for closing ```)
        if len(current_message) + len(line) + 10 > 1900:  # 1900 to be safe
            # Close current message and start a new one
            current_message += "```"
            messages.append(current_message)
            
            # Start new message
            current_message = "🏁 **RACE RESULTS (continued)**\n\n"
            current_message += "```\n"
            current_message += f"{'Pos':<5} {'Player':<20} {'Bounty':>10}\n"
            current_message += "─" * 40 + "\n"
        
        current_message += line
    
    # Close final message
    current_message += "```"
    messages.append(current_message)
    
    return messages

# =============================================================================
# BOT EVENTS
# =============================================================================

@bot.event
async def on_ready():
    """Called when bot successfully connects"""
    print(f'✅ Bot logged in as {bot.user.name} (ID: {bot.user.id})')
    print(f'🔧 OCR Engine: {OCR_ENGINE}')
    
    # Sync slash commands
    try:
        if GUILD_ID:
            # Sync to specific guild (instant, for testing)
            bot.tree.copy_global_to(guild=GUILD_ID)
            await bot.tree.sync(guild=GUILD_ID)
            print(f'✅ Slash commands synced to guild {GUILD_ID.id}')
        else:
            # Sync globally (takes up to 1 hour)
            await bot.tree.sync()
            print('✅ Slash commands synced globally (may take up to 1 hour)')
    except Exception as e:
        print(f'❌ Failed to sync commands: {e}')
    
    print('Ready to process Marbles on Stream screenshots!')

# =============================================================================
# SLASH COMMANDS
# =============================================================================

@bot.tree.command(name="submit_marbles", description="Submit Marbles on Stream screenshot(s) - upload multiple for large games")
@app_commands.describe(
    screenshot1="First screenshot (required)",
    screenshot2="Second screenshot (optional - for games with many players)",
    screenshot3="Third screenshot (optional)",
    screenshot4="Fourth screenshot (optional)",
    screenshot5="Fifth screenshot (optional)"
)
async def submit_marbles(
    interaction: discord.Interaction, 
    screenshot1: discord.Attachment,
    screenshot2: Optional[discord.Attachment] = None,
    screenshot3: Optional[discord.Attachment] = None,
    screenshot4: Optional[discord.Attachment] = None,
    screenshot5: Optional[discord.Attachment] = None
):
    """
    Slash command to submit Marbles screenshot(s).
    Supports multiple screenshots for large games - will automatically merge overlapping players.
    """
    
    # Collect all provided screenshots
    screenshots = [ss for ss in [screenshot1, screenshot2, screenshot3, screenshot4, screenshot5] if ss]

    # Validate all are images
    for screenshot in screenshots:
        if not any(screenshot.filename.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp']):
            await interaction.response.send_message(
                f"❌ {screenshot.filename} is not an image file. Please upload PNG, JPG, GIF, etc.",
                ephemeral=True
            )
            return
    
    # Defer response (processing might take a while)
    await interaction.response.defer(ephemeral=False)
    
    try:
        all_parsed_data = []
        
        # Process each screenshot
        for idx, screenshot in enumerate(screenshots, 1):
            await interaction.followup.send(f"🔍 Processing screenshot {idx}/{len(screenshots)}...", ephemeral=True)
            
            # Download image
            image = await download_image_from_attachment(screenshot)
            if not image:
                await interaction.followup.send(f"❌ Failed to download screenshot {idx}.", ephemeral=True)
                continue
            
            # Perform OCR
            ocr_text = perform_ocr(image)
            
            if not ocr_text:
                await interaction.followup.send(f"❌ Could not extract text from screenshot {idx}.", ephemeral=True)
                continue
            
            # Parse results
            parsed_data = parse_marbles_screenshot(ocr_text)
            
            if not parsed_data:
                await interaction.followup.send(
                    f"❌ Could not parse screenshot {idx}. Skipping...",
                    ephemeral=True
                )
                continue
            
            all_parsed_data.append(parsed_data)
        
        if not all_parsed_data:
            await interaction.followup.send(
                "❌ Could not parse any screenshots. Make sure they are Marbles on Stream end screens!",
                ephemeral=True
            )
            return
        
        # Merge multiple screenshots if needed
        if len(all_parsed_data) > 1:
            merged_data = merge_screenshot_data(all_parsed_data)
            await interaction.followup.send(
                f"✅ Merged {len(all_parsed_data)} screenshots into {merged_data['total_players']} unique players!",
                ephemeral=True
            )
        else:
            merged_data = all_parsed_data[0]
        
        # Update bounty board
        update_bounty_board(merged_data)
        
        # Send results (publicly in channel) - split into multiple messages if needed
        game_results_messages = format_game_results(merged_data)
        
        await interaction.followup.send(f"✅ Processed by {interaction.user.mention}")
        
        for result_msg in game_results_messages:
            await interaction.followup.send(result_msg)
        
        leaderboard_messages = format_leaderboard()
        for leaderboard_msg in leaderboard_messages:
            await interaction.followup.send(leaderboard_msg)
        
    except Exception as e:
        print(f"Error processing screenshot: {e}")
        import traceback
        traceback.print_exc()
        await interaction.followup.send(f"❌ An error occurred while processing: {str(e)}", ephemeral=True)

@bot.tree.command(name="leaderboard", description="Display the current bounty leaderboard")
async def leaderboard_slash(interaction: discord.Interaction):
    """Show current bounty leaderboard"""
    leaderboard_messages = format_leaderboard()
    await interaction.response.send_message(leaderboard_messages[0])
    
    # Send additional messages if leaderboard is split
    for leaderboard_msg in leaderboard_messages[1:]:
        await interaction.followup.send(leaderboard_msg)

@bot.tree.command(name="bounty", description="Check a player's current bounty")
@app_commands.describe(player="The player name to look up")
async def bounty_slash(interaction: discord.Interaction, player: str):
    """Show bounty for a specific player"""
    # Try exact match first
    if player in bounty_board:
        bounty = bounty_board[player]
        await interaction.response.send_message(f"💰 **{player}** has a bounty of **{bounty:+}** points!")
        return
    
    # Try case-insensitive match
    player_lower = player.lower()
    for name, bounty in bounty_board.items():
        if name.lower() == player_lower:
            await interaction.response.send_message(f"💰 **{name}** has a bounty of **{bounty:+}** points!")
            return
    
    await interaction.response.send_message(f"❌ Player **{player}** not found in bounty board.", ephemeral=True)

@bot.tree.command(name="edit_leaderboard", description="Edit the leaderboard - remove incorrect players (Admin only)")
@app_commands.checks.has_permissions(administrator=True)
async def edit_leaderboard(interaction: discord.Interaction):
    """Allow admins to remove incorrect players from the leaderboard"""
    if not bounty_board:
        await interaction.response.send_message(
            "❌ The leaderboard is empty. No players to edit.",
            ephemeral=True
        )
        return
    
    # Create interactive view with pagination
    view = LeaderboardEditView()
    
    # Get sorted leaderboard
    sorted_players = sorted(bounty_board.items(), key=lambda x: x[1], reverse=True)
    players_per_page = 25
    page_players = sorted_players[:players_per_page]
    total_pages = (len(sorted_players) + players_per_page - 1) // players_per_page
    
    player_list = "\n".join([
        f"#{rank} {name} ({bounty:+})" 
        for rank, (name, bounty) in enumerate(page_players, 1)
    ])
    
    content = f"**🛠️ Edit Leaderboard**\n\n"
    content += f"Total Players: {len(sorted_players)}\n"
    content += f"Page 1/{total_pages}\n\n"
    content += f"**Players on this page:**\n{player_list}\n\n"
    content += "Select players to remove, use ◀ Next ▶ to navigate, or click Done:"
    
    await interaction.response.send_message(
        content=content,
        view=view,
        ephemeral=False
    )

@edit_leaderboard.error
async def edit_leaderboard_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """Handle permission errors"""
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message(
            "❌ You need Administrator permissions to edit the leaderboard!",
            ephemeral=True
        )

@bot.tree.command(name="edit_last_game", description="Edit the last game results - remove incorrect players (Admin only)")
@app_commands.checks.has_permissions(administrator=True)
async def edit_last_game(interaction: discord.Interaction):
    """Allow admins to remove incorrect players from last game"""
    global last_game_data
    
    if not last_game_data:
        await interaction.response.send_message(
            "❌ No recent game data found. Submit a game first using `/submit_marbles`.",
            ephemeral=True
        )
        return
    
    # Create interactive view with pagination
    view = PlayerRemovalView(last_game_data)
    
    # Show first page
    players_per_page = 25
    page_players = last_game_data['results'][:players_per_page]
    total_pages = (len(last_game_data['results']) + players_per_page - 1) // players_per_page
    
    player_list = "\n".join([f"#{pos} - {name}" for name, pos in page_players])
    
    content = f"**🛠️ Edit Last Game Results**\n\n"
    content += f"Total Players: {last_game_data['total_players']}\n"
    content += f"Page 1/{total_pages}\n\n"
    content += f"**Players on this page:**\n{player_list}\n\n"
    content += "Select players to remove, use ◀ Next ▶ to navigate, or click Done:"
    
    await interaction.response.send_message(
        content=content,
        view=view,
        ephemeral=False
    )

@edit_last_game.error
async def edit_last_game_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """Handle permission errors"""
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message(
            "❌ You need Administrator permissions to edit game results!",
            ephemeral=True
        )

@bot.tree.command(name="remove_player", description="Remove a player from the bounty board (Admin only)")
@app_commands.describe(player="Player name to remove")
@app_commands.checks.has_permissions(administrator=True)
async def remove_player(interaction: discord.Interaction, player: str):
    """Remove a player completely from the bounty board"""
    global bounty_board
    
    # Try exact match
    if player in bounty_board:
        bounty = bounty_board[player]
        del bounty_board[player]
        save_bounty_board(bounty_board)
        await interaction.response.send_message(
            f"✅ Removed **{player}** (had {bounty:+} bounty) from the leaderboard!"
        )
        return
    
    # Try case-insensitive
    player_lower = player.lower()
    for name in list(bounty_board.keys()):
        if name.lower() == player_lower:
            bounty = bounty_board[name]
            del bounty_board[name]
            save_bounty_board(bounty_board)
            await interaction.response.send_message(
                f"✅ Removed **{name}** (had {bounty:+} bounty) from the leaderboard!"
            )
            return
    
    await interaction.response.send_message(
        f"❌ Player **{player}** not found in bounty board.",
        ephemeral=True
    )

@remove_player.error
async def remove_player_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """Handle permission errors"""
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message(
            "❌ You need Administrator permissions to remove players!",
            ephemeral=True
        )

@bot.tree.command(name="reset_bounties", description="Reset the entire bounty board (Admin only)")
@app_commands.checks.has_permissions(administrator=True)
async def reset_slash(interaction: discord.Interaction):
    """Reset the entire bounty board (Admin only)"""
    global bounty_board
    bounty_board = {}
    save_bounty_board(bounty_board)
    await interaction.response.send_message("🔄 Bounty board has been reset!")

@reset_slash.error
async def reset_slash_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """Handle permission errors for reset command"""
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ You need Administrator permissions to reset the bounty board!", ephemeral=True)

@bot.tree.command(name="help_marbles", description="Show bot help and information")
async def help_slash(interaction: discord.Interaction):
    """Show bot help"""
    help_text = """
🎮 **MARBLES ON STREAM BOT HELP** 🎮

**How to Submit Results:**
1. Type `/submit_marbles`
2. Upload your Marbles on Stream end screen screenshot(s)
   - For large games (50+ players), upload multiple screenshots!
   - The bot will automatically merge overlapping players
3. The bot will process and post results publicly!

**Commands:**
`/leaderboard` - Show current bounty rankings
`/bounty <player>` - Show a specific player's bounty
`/edit_last_game` - Edit last game, remove incorrect players (Admin)
`/remove_player <name>` - Remove a player from leaderboard (Admin)
`/reset_bounties` - Reset the bounty board (Admin only)
`/help_marbles` - Show this help message

**Multi-Screenshot Support:**
For games with many players, upload multiple screenshots in order:
• Screenshot 1: Players 1-25
• Screenshot 2: Players 22-43 (bot detects overlap)
• Screenshot 3: Players 43-50 (bot continues numbering)
The bot will merge them automatically!

**Bounty Scoring (Position-Based):**
• Placement Formula: ((N - position + 1) - N/2) × 20
  - Top positions get positive points
  - Middle positions get ~0 points
  - Bottom positions get negative points
• 1st place bonus: +200 points

**Example (10 players):**
• 1st place: +200 + placement ≈ +300 points
• 5th place: placement ≈ +10 points
• 10th place: placement ≈ -90 points

**✨ OCR Error Handling:**
The bot automatically handles common OCR misreads:
• "Tlme" or "T1me" → recognizes as "Time"
• "P0ints" or "Polnts" → recognizes as "Points"
Works even with poor screenshot quality!
"""
    await interaction.response.send_message(help_text, ephemeral=True)

# =============================================================================
# LEGACY TEXT COMMANDS (Optional - for backwards compatibility)
# =============================================================================

@bot.command(name='leaderboard')
async def show_leaderboard(ctx):
    """Display current bounty leaderboard (text command)"""
    leaderboard_messages = format_leaderboard()
    for leaderboard_msg in leaderboard_messages:
        await ctx.send(leaderboard_msg)

@bot.command(name='bounty')
async def show_bounty(ctx, *, player_name: str):
    """Show bounty for a specific player (text command)"""
    if player_name in bounty_board:
        bounty = bounty_board[player_name]
        await ctx.send(f"💰 **{player_name}** has a bounty of **{bounty:+}** points!")
        return
    
    player_lower = player_name.lower()
    for name, bounty in bounty_board.items():
        if name.lower() == player_lower:
            await ctx.send(f"💰 **{name}** has a bounty of **{bounty:+}** points!")
            return
    
    await ctx.send(f"❌ Player **{player_name}** not found in bounty board.")

# =============================================================================
# RUN BOT
# =============================================================================

if __name__ == '__main__':
    print("🚀 Starting Marbles on Stream Discord Bot (Position-Based Ranking)...")
    print("⚠️  Make sure you've set BOT_TOKEN!")
    
    if BOT_TOKEN == 'YOUR_BOT_TOKEN_HERE':
        print("❌ ERROR: Please set your bot token in the code!")
        exit(1)
    
    try:
        bot.run(BOT_TOKEN)
    except discord.LoginFailure:
        print("❌ ERROR: Invalid bot token!")
    except Exception as e:
        print(f"❌ ERROR: {e}")