# patcher.py
import os

print("⚡ Starting local structural hotfix tool...")

# --- FIXING API_HANDLERS.PY ---
if os.path.exists("api_handlers.py"):
    with open("api_handlers.py", "r", encoding="utf-8") as f:
        api_code = f.read()

    # Targets the card serialization block inside handle_get_game_state
    old_block = """    my_cards = []
    for idx in my_card_indices:
        marked = db.get_marked_numbers(game_id, idx)
        my_cards.append(_serialize_card(idx, called_numbers, marked))"""

    new_block = """    my_cards = []
    for c in my_card_indices:
        # Secure type extraction for sqlite3.Row vs direct integer indices
        if isinstance(c, dict): card_idx = c.get("card_index")
        elif hasattr(c, "keys"): card_idx = c["card_index"]
        else: card_idx = int(c)
        
        raw_marked = db.get_marked_numbers(game_id, card_idx)
        marked_numbers = []
        if raw_marked:
            for item in raw_marked:
                if isinstance(item, (int, str)): marked_numbers.append(int(item))
                elif isinstance(item, (list, tuple)) and item: marked_numbers.append(int(item[0]))
                elif hasattr(item, "keys"): marked_numbers.append(int(item.get("number", item[0])))

        if is_auto:
            card_data = bingo.get_card(card_idx)
            flat_card = [num for row in card_data for num in row]
            auto_marked = [num for num in flat_card if num in called_numbers]
            my_cards.append(_serialize_card(card_idx, called_numbers, auto_marked))
        else:
            # Manual Mode: Called numbers passed as empty so frontend ignores global ticks
            my_cards.append(_serialize_card(card_idx, [], marked_numbers))"""

    if old_block in api_code:
        api_code = api_code.replace(old_block, new_block)
        with open("api_handlers.py", "w", encoding="utf-8") as f:
            f.write(api_code)
        print("✅ api_handlers.py: Manual highlighting logic patched successfully.")
    else:
        print("⚠️ api_handlers.py: target section not found or already modified.")
else:
    print("❌ api_handlers.py was not found in this directory.")


# --- FIXING BOT.PY CALLBACKS ---
if os.path.exists("bot.py"):
    with open("bot.py", "r", encoding="utf-8") as f:
        bot_code = f.read()

    # Fix the breaking mark_player_number code inside game_check_all
    old_check_all = """    for c in player_cards:
        card_data = bingo.get_card(c["card_index"])
        
        # \u2705 FIX: Flatten the 2D matrix so we iterate numbers, not rows!
        for row in card_data:
            for num in row:
                if num and num in called_numbers:
                    db.mark_player_number(game_id, user_id, c["card_index"], num)"""

    new_check_all = """    for c in player_cards:
        card_idx = c["card_index"] if isinstance(c, dict) or hasattr(c, "keys") else c
        card_data = bingo.get_card(card_idx)
        current_marked = list(db.get_marked_numbers(game_id, card_idx))
        cleaned_marked = [int(m[0] if isinstance(m, (list, tuple)) else m) for m in current_marked]
        has_updates = False

        for row in card_data:
            for num in row:
                if num and num in called_numbers and num not in cleaned_marked:
                    cleaned_marked.append(num)
                    has_updates = True
        if has_updates:
            db.update_marked_numbers(game_id, card_idx, sorted(cleaned_marked))"""

    if old_check_all in bot_code:
        bot_code = bot_code.replace(old_check_all, new_check_all)
        
        # Also clean up the auto win database updates
        bot_code = bot_code.replace("db.update_player_auto_win(game_id, user_id, new_setting)", "db.set_auto_win(game_id, user_id, new_setting)")
        
        with open("bot.py", "w", encoding="utf-8") as f:
            f.write(bot_code)
        print("✅ bot.py: Interactive button callbacks patched successfully.")
    else:
        print("⚠️ bot.py: target loop section not found or already modified.")
else:
    print("❌ bot.py was not found in this directory.")

print("🏁 Patching operation complete.")