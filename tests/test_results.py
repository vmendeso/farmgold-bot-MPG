import unittest

from farmbot.models import LogMarker, ParsedBattle, UntimedDefeat
from farmbot.results import LogTracker, parse_battle, parse_log_entry


def battle(identifier: int, duration: float = 10.0) -> ParsedBattle:
    return ParsedBattle("4-8", duration, identifier, identifier * 2, "victory")


class ParserTests(unittest.TestCase):
    def test_reference_screenshot_row(self):
        result = parse_battle("4-8 concluída · 0:43.37 · 🏆 +1,362 · +3,055 EXP")
        self.assertIsNotNone(result)
        self.assertEqual((result.stage, result.gold, result.exp, result.outcome),
                         ("4-8", 1362, 3055, "victory"))
        self.assertAlmostEqual(result.duration, 43.37)

    def test_live_ocr_hyphen_delimiters_currency_icon_and_localized_exp(self):
        result = parse_battle("4-7 concluida - 0:43.10 - € +1,362 - +2.965 EXP")
        self.assertIsInstance(result, ParsedBattle)
        self.assertEqual((result.stage, result.duration, result.gold, result.exp),
                         ("4-7", 43.10, 1362, 2965))

    def test_hyphen_delimiter_support_does_not_accept_negative_rewards(self):
        for rewards in ("€ -1,362 - +2.965 EXP", "€ +1,362 - -2.965 EXP",
                        "€ -+1,362 - +2.965 EXP", "€ +1,36 - +2.965 EXP"):
            with self.subTest(rewards=rewards):
                self.assertIsNone(parse_battle("4-7 concluida - 0:43.10 - " + rewards))

    def test_noisy_middle_dot_preserves_numbers_and_rejects_attached_signs(self):
        text = "2-6 concluida - 1:11.44~- +1,849 - +5,050 EXP"
        result = parse_battle(text)
        self.assertEqual((result.duration, result.gold, result.exp, result.raw),
                         (71.44, 1849, 5050, text))
        for prefix in ("~-", "-- ", "+- ", "~-1 "):
            with self.subTest(prefix=prefix):
                self.assertIsNone(parse_battle("2-6 concluida - 1:11.44 " + prefix + "+1,849 - +5,050 EXP"))

    def test_colon_reward_separator_preserves_exact_duration_and_rewards(self):
        for duration, expected in (("0:42.95", 42.95), ("0:42,95", 42.95),
                                   ("1:02.345", 62.345), ("0:42", 42.0)):
            for spacing in (" ", ""):
                with self.subTest(duration=duration, spacing=spacing):
                    text = f"4-7 concluida - {duration}:{spacing}+1.362 : +2.965 EXP"
                    result = parse_battle(text)
                    self.assertIsInstance(result, ParsedBattle)
                    self.assertEqual((result.stage, result.gold, result.exp), ("4-7", 1362, 2965))
                    self.assertAlmostEqual(result.duration, expected)
                    self.assertEqual(result.raw, text)

    def test_colon_separator_does_not_repair_malformed_time_or_rewards(self):
        rows = [
            "4-7 concluida - 0:42:95: +1.362 : +2.965 EXP",
            "4-7 concluida - 0:42.95:7 +1.362 : +2.965 EXP",
            "4-7 concluida - 0:42.9512: +1.362 : +2.965 EXP",
            "4-7 concluida - 0:+42.95: +1.362 : +2.965 EXP",
            "4-7 concluida - 0:42.95: -1.362 : +2.965 EXP",
            "4-7 concluida - 0:42.95: -+1.362 : +2.965 EXP",
            "4-7 concluida - 0:42.95: +1.362 : -2.965 EXP",
            "4-7 concluida - 0:42.95: +1,36 : +2.965 EXP",
            "4-7 concluida - 0:42.95: +1.362 : +2,96 EXP",
            "4-7 concluida - 0:42.95: 8 +1.362 : +2.965 EXP",
            "4-7 concluida - 0:42.95: +1.362",
        ]
        for row in rows:
            with self.subTest(row=row):
                self.assertIsNone(parse_battle(row))

    def test_localized_integer_separators_and_duration(self):
        for gold, exp in [("1.362", "3.055"), ("1 362", "3 055"), ("1362", "3055")]:
            with self.subTest(gold=gold):
                result = parse_battle(f"4-8 concluida · 1:03,7 · +{gold} · +{exp} EXP")
                self.assertEqual((result.gold, result.exp), (1362, 3055))
                self.assertAlmostEqual(result.duration, 63.7)

    def test_explicit_defeat_without_rewards(self):
        result = parse_battle("4-8 derrota · 0:12.50")
        self.assertEqual((result.outcome, result.gold, result.exp, result.duration),
                         ("defeat", 0, 0, 12.5))

    def test_incomplete_ambiguous_and_invalid_rows_are_not_victories(self):
        rows = [
            "4-8 · 0:43.37 · +1,362 · +3,055 EXP",
            "4-8 não concluída · 0:43.37 · +1,362 · +3,055 EXP",
            "4-8 concluída · 0:43.37 · +1,362",
            "4-8 concluída · 0:43.37 · +1,36 · +3,055 EXP",
            "4-8 concluída · 0:43.37 · +1,362 · +3,05 EXP",
            "4-8 concluída · 0:00.00 · +1,362 · +3,055 EXP",
            "4-8 concluída · 0:99.00 · +1,362 · +3,055 EXP",
            "4-8 concluída derrota · 0:43.37 · +1,362 · +3,055 EXP",
            "4-8 derrota · 0:12.50 · +1,3",
            "4-8 concluída · 0:43.37 · -1,362 · +3,055 EXP",
        ]
        for row in rows:
            with self.subTest(row=row):
                self.assertIsNone(parse_battle(row))


class NotificationParserTests(unittest.TestCase):
    def test_exact_untimed_defeat_is_typed_without_inventing_missing_values(self):
        result = parse_log_entry("1-8 falhou")
        self.assertIsInstance(result, UntimedDefeat)
        self.assertEqual((result.stage, result.raw), ("1-8", "1-8 falhou"))
        self.assertIsNone(parse_battle(result.raw))
        self.assertFalse(any(hasattr(result, field) for field in ("duration", "gold", "exp")))
        for text in ("1-8 falhou 0:", "1-8 falhou +12", "1-8 falhou EXP", "1-8 falh",
                     "1-8 concluida", "1-8 nao falhou", "1-8 falhou 1-9", "0-8 falhou"):
            with self.subTest(text=text):
                self.assertIsNone(parse_log_entry(text))

    def test_known_loot_messages_are_markers_without_rewards_or_battle_time(self):
        for text in ("Caixa obtida", "Caixa de Chefe obtida", "Caixa de Chefe aberta",
                     "Você obteve Escudo do Guardião x1", "Voce obteve Poção Grande x12."):
            with self.subTest(text=text):
                marker = parse_log_entry(text)
                self.assertIsInstance(marker, LogMarker)
                self.assertEqual(marker.raw, text)
                self.assertEqual(marker.duration, 0)
                self.assertEqual(marker.fingerprint[0], "notification")

    def test_marker_fingerprints_ignore_case_accents_and_ocr_whitespace(self):
        first = parse_log_entry("Você obteve Escudo do Guardião x1")
        second = parse_log_entry("  VOCE  obteve Escudo do Guardiao x1. ")
        self.assertEqual(first.fingerprint, second.fingerprint)

    def test_observed_box_prefix_ocr_variants_share_a_stable_fingerprint(self):
        expected = parse_log_entry("Caixa de Chefe obtida")
        for variant in ("Calxa de Chefe obtida", "Caixa de Chafe obtida", "Calxa de Chafe obtida"):
            with self.subTest(variant=variant):
                marker = parse_log_entry(variant)
                self.assertIsInstance(marker, LogMarker)
                self.assertEqual(marker.fingerprint, expected.fingerprint)
                self.assertEqual(marker.raw, variant)

    def test_unknown_and_incomplete_battle_rows_never_become_markers(self):
        for text in ("texto qualquer", "Caixa", "Você obteve", "Você obteve Escudo x",
                     "4-8 concluída · 0:43.37 · +1,362",
                     "Caixa obtida · 4-8 concluída · 0:43.37", "Caixa obtida +1,362 EXP"):
            with self.subTest(text=text):
                self.assertIsNone(parse_log_entry(text))

    def test_explicit_extra_pattern_does_not_relax_battle_parsing(self):
        marker = parse_log_entry("Missão diária completa", [r"missao diaria completa"])
        self.assertIsInstance(marker, LogMarker)
        self.assertIsNone(parse_log_entry("4-8 concluída · 0:43.37 · +1,362", [r".+"]))
        self.assertIsInstance(parse_log_entry("Caixa obtida", [r"missao diaria completa"]), LogMarker)


class TrackerTests(unittest.TestCase):
    def test_untimed_defeats_preserve_baseline_and_identical_occurrences(self):
        defeat = UntimedDefeat("1-8", "1-8 falhou")
        tracker = LogTracker(order="oldest_first")
        baseline = [battle(1), defeat, battle(2)]
        tracker.prime(baseline)
        self.assertEqual(tracker.update(baseline, 1), [])
        self.assertEqual(tracker.update([battle(2), defeat, defeat], 1), [defeat, defeat])

    def test_untimed_defeat_does_not_exempt_following_battle_duration(self):
        tracker = LogTracker(order="oldest_first")
        baseline = [battle(1), battle(2), battle(3)]
        tracker.prime(baseline)
        self.assertEqual(tracker.update([battle(3), UntimedDefeat("1-8", "1-8 falhou"), battle(4)], 2), [])
        self.assertEqual(tracker.diagnostics, "no_feasible_overlap_rebased")

    def test_baseline_and_repeated_capture_never_count_as_new(self):
        tracker = LogTracker()
        rows = [battle(i) for i in range(5, 0, -1)]
        tracker.prime(rows)
        self.assertEqual(tracker.update(rows, 2), [])
        self.assertEqual(tracker.update([battle(6), *rows[:-1]], 2), [battle(6)])
        self.assertEqual(tracker.update([battle(6), *rows[:-1]], 2), [])

    def test_two_identical_new_lines_are_two_occurrences(self):
        tracker = LogTracker(order="oldest_first")
        tracker.prime([battle(i) for i in range(1, 6)])
        additions = tracker.update([battle(3), battle(4), battle(5), battle(6), battle(6)], 12)
        self.assertEqual(additions, [battle(6), battle(6)])
        self.assertEqual(len(additions), 2)

    def test_growing_log_counts_new_occurrences_including_identical_lines(self):
        tracker = LogTracker()
        tracker.prime([battle(1)])
        self.assertEqual(tracker.update([battle(1), battle(1)], 10), [battle(1)])
        self.assertEqual(tracker.update([battle(2), battle(1), battle(1)], 10), [battle(2)])
        self.assertEqual(tracker.update([battle(2), battle(1), battle(1)], 1), [])

    def test_changed_row_count_without_complete_prefix_is_not_new(self):
        tracker = LogTracker(order="oldest_first")
        tracker.prime([battle(1), battle(3)])
        self.assertEqual(tracker.update([battle(1), battle(2), battle(3)], 10), [])
        self.assertEqual(tracker.diagnostics, "row_count_changed_without_anchor_rebased")

    def test_growing_log_after_long_gap_is_only_a_baseline(self):
        tracker = LogTracker(max_gap_seconds=20)
        tracker.prime([battle(1)])
        self.assertEqual(tracker.update([battle(2), battle(1)], 30), [])

    def test_identical_rows_at_baseline_retain_all_five_positions(self):
        tracker = LogTracker(order="oldest_first")
        tracker.prime([battle(1)] * 5)
        self.assertEqual(len(tracker._previous), 5)
        self.assertEqual(tracker.update([battle(1)] * 5, 12), [])
        self.assertIn("cannot_distinguish_new_occurrences", tracker.diagnostics)
        # Distinct anchors become observable; timing establishes two new rows.
        self.assertEqual(tracker.update([battle(1)] * 3 + [battle(2), battle(3)], 12),
                         [battle(2), battle(3)])

    def test_missing_ocr_row_is_not_movement(self):
        tracker = LogTracker(order="oldest_first")
        original = [battle(i) for i in range(5)]
        tracker.prime(original)
        self.assertEqual(tracker.update(original[1:], 1), [])
        self.assertEqual(tracker.diagnostics, "partial_ocr")
        self.assertEqual(tracker.update(original, 1), [])
        self.assertEqual(tracker.update(original[1:] + [battle(5)], 1), [battle(5)])

    def test_no_overlap_rebases_without_counting_old_rows(self):
        tracker = LogTracker(order="oldest_first")
        tracker.prime([battle(i) for i in range(5)])
        self.assertEqual(tracker.update([battle(i) for i in range(10, 15)], 60), [])
        self.assertEqual(tracker.update([battle(i) for i in range(11, 16)], 10), [battle(15)])

    def test_ambiguous_repeating_sequence_is_rejected(self):
        tracker = LogTracker(order="oldest_first")
        tracker.prime([battle(1), battle(2), battle(1), battle(2), battle(1)])
        self.assertEqual(tracker.update([battle(2), battle(1), battle(2), battle(1), battle(2)], 35), [])
        self.assertEqual(tracker.diagnostics, "ambiguous_overlap_rebased")

    def test_time_can_resolve_repeating_sequence_without_discarding_occurrences(self):
        tracker = LogTracker(order="oldest_first")
        tracker.prime([battle(1), battle(2), battle(1), battle(2), battle(1)])
        self.assertEqual(tracker.update([battle(2), battle(1), battle(2), battle(1), battle(2)], 1),
                         [battle(2)])

    def test_long_observation_gap_is_not_counted(self):
        tracker = LogTracker(order="oldest_first", max_gap_seconds=20)
        tracker.prime([battle(i) for i in range(5)])
        tracker.update([], 15)
        self.assertEqual(tracker.update([battle(i) for i in range(1, 6)], 15), [])
        self.assertEqual(tracker.diagnostics, "observation_gap_rebased")

    def test_snapshot_simulation_counts_occurrences_exactly_once(self):
        # This sequence contains equal adjacent results, as in real repeated
        # farming. Captures repeat between completions and occasionally lose a
        # row to OCR; each provable newly completed occurrence is counted once.
        sequence = [battle(i) for i in (1, 2, 3, 4, 5, 6, 6, 7, 8, 8, 8, 9, 10)]
        tracker = LogTracker(order="oldest_first")
        tracker.prime(sequence[:5])
        received = []
        for index in range(5, len(sequence)):
            snapshot = sequence[index - 4:index + 1]
            received.extend(tracker.update(snapshot[:-1], 1))
            received.extend(tracker.update(snapshot, 9))
            received.extend(tracker.update(snapshot, 0.1))
        self.assertEqual(received, sequence[5:])

    def test_mixed_notification_and_battle_sequence_preserves_every_occurrence(self):
        obtained = LogMarker("Caixa de Chefe obtida")
        opened = LogMarker("Caixa de Chefe aberta")
        item = LogMarker("Você obteve Escudo do Guardião x1")
        repeated = battle(6)
        sequence = [battle(1), obtained, battle(2), obtained, battle(3),
                    opened, item, repeated, obtained, repeated]
        for order in ("oldest_first", "newest_first"):
            with self.subTest(order=order):
                tracker = LogTracker(order=order)
                orient = lambda rows: rows if order == "oldest_first" else list(reversed(rows))
                tracker.prime(orient(sequence[:5]))
                received = []
                for index in range(5, len(sequence)):
                    snapshot = orient(sequence[index - 4:index + 1])
                    received.extend(tracker.update(snapshot, 1))
                    self.assertEqual(tracker.update(snapshot, 0.1), [])
                self.assertEqual(received, sequence[5:])
                self.assertEqual([entry for entry in received if isinstance(entry, ParsedBattle)],
                                 [repeated, repeated])

    def test_notification_before_first_new_battle_does_not_require_full_battle_time(self):
        tracker = LogTracker(order="oldest_first")
        baseline = [battle(i) for i in range(5)]
        tracker.prime(baseline)
        opened = LogMarker("Caixa de Chefe aberta")
        finished = battle(5, duration=43.37)
        self.assertEqual(tracker.update([*baseline[2:], opened, finished], 1), [opened, finished])

    def test_multiple_new_battles_still_need_time_even_with_notification_between(self):
        tracker = LogTracker(order="oldest_first")
        baseline = [battle(i) for i in range(5)]
        tracker.prime(baseline)
        opened = LogMarker("Caixa de Chefe aberta")
        self.assertEqual(tracker.update([baseline[-1], opened, battle(5), opened, battle(6)], 1), [])

    def test_identical_notification_rows_remain_distinct_occurrences(self):
        tracker = LogTracker(order="oldest_first")
        baseline = [battle(i) for i in range(5)]
        tracker.prime(baseline)
        marker = LogMarker("Caixa obtida")
        self.assertEqual(tracker.update([*baseline[2:], marker, marker], 0.1), [marker, marker])


if __name__ == "__main__":
    unittest.main()
