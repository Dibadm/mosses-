# test_bingo.py
# ============================================
# HABESHA BET - BINGO LOGIC TESTS
# ============================================

import pytest

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend import bingo


class TestCardGeneration:
    def test_generate_card_pool_size(self):
        pool = bingo.generate_card_pool(size=10, seed=42)
        assert len(pool) == 10

    def test_generate_card_pool_deterministic(self):
        pool1 = bingo.generate_card_pool(size=10, seed=42)
        pool2 = bingo.generate_card_pool(size=10, seed=42)
        assert pool1 == pool2

    def test_generate_card_pool_unique(self):
        pool = bingo.generate_card_pool(size=50, seed=42)
        seen = set()
        for card in pool:
            fp = tuple(sorted(n for col in card for n in col if n != bingo.FREE_SPACE))
            assert fp not in seen
            seen.add(fp)

    def test_generate_card_pool_structure(self):
        pool = bingo.generate_card_pool(size=5, seed=42)
        for card in pool:
            assert len(card) == 5
            for col in card:
                assert len(col) == 5
            assert card[2][2] == bingo.FREE_SPACE

    def test_card_pool_secrets_seed(self):
        pool = bingo.generate_card_pool(size=10)
        assert len(pool) == 10


class TestWinDetection:
    def setup_method(self):
        self.card = bingo.generate_card_pool(size=1, seed=42)[0]
        # Use numbers from non-diagonal, non-row/column positions to assert no win
        self.called = {self.card[0][1], self.card[1][0], self.card[2][1], self.card[3][0], self.card[4][1]}

    def test_no_win(self):
        assert not bingo.check_win(self.card, self.called)

    def test_line_win(self):
        called = set()
        for col in range(5):
            called.add(self.card[col][0])
        assert bingo.check_line_win(self.card, called)

    def test_corners_win(self):
        corners = [
            self.card[0][0], self.card[4][0],
            self.card[0][4], self.card[4][4],
        ]
        called = set(corners)
        assert bingo.check_corners_win(self.card, called)

    def test_free_space_counts(self):
        called = {bingo.FREE_SPACE}
        for col in range(5):
            for row in range(5):
                if (col, row) != (2, 2):
                    called.add(self.card[col][row])
        assert bingo.check_win(self.card, called)


class TestCallSequence:
    def test_generate_call_sequence_length(self):
        seq = bingo.generate_call_sequence(seed=42)
        assert len(seq) == 75
        assert set(seq) == set(range(1, 76))

    def test_generate_call_sequence_seeded(self):
        seq1 = bingo.generate_call_sequence(seed=42)
        seq2 = bingo.generate_call_sequence(seed=42)
        assert seq1 == seq2

    def test_generate_call_sequence_different_seeds(self):
        seq1 = bingo.generate_call_sequence(seed=42)
        seq2 = bingo.generate_call_sequence(seed=99)
        assert seq1 != seq2


class TestRenderers:
    def test_render_card_text_no_crash(self):
        card = bingo.generate_card_pool(size=1, seed=42)[0]
        called = set()
        text = bingo.render_card_text(card, called)
        assert "B" in text
        assert "I" in text

    def test_render_number_grid(self):
        called = {1, 15, 30, 45, 60, 75}
        text = bingo.render_number_grid(called)
        assert "[ 1]" in text


class TestAmharic:
    def test_number_names(self):
        assert bingo.number_to_amharic(1) == "አንድ"
        assert bingo.number_to_amharic(10) == "አስር"
        assert bingo.number_to_amharic(20) == "ሃያ"
        assert bingo.number_to_amharic(75) == "ሰባ አምስት"

    def test_number_to_letter(self):
        assert bingo.number_to_letter(1) == "B"
        assert bingo.number_to_letter(15) == "B"
        assert bingo.number_to_letter(16) == "I"
        assert bingo.number_to_letter(75) == "O"


class TestMasking:
    def test_mask_username(self):
        assert bingo.mask_username("Abdi Mohammed", 5) == "@Abdi ***"
        assert bingo.mask_username("", 3) == "@***"
