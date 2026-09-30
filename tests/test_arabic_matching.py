import unittest

from ML.JsonTransformation import (
    normalize,
    restore_arabic_ocr,
    search_page_lines,
    search_raw_kv,
    _validation_status,
    _line_label_match,
    parse_extraction_rule_specs,
    parse_extraction_rules,
    _translation_requested,
)


class ArabicMatchingTests(unittest.TestCase):
    def test_normalize_unifies_arabic_forms_and_digits(self):
        self.assertEqual(
            normalize("إِجْمَالِيّ ـ ٢٠٢٦"),
            normalize("اجمالي 2026"),
        )
        self.assertEqual(normalize("ى"), normalize("ي"))
        self.assertEqual(normalize("ة"), normalize("ه"))

    def test_restore_does_not_reverse_arabic_indic_digits(self):
        value = "٢٠٢٦ ربمتبس ١١"
        restored = restore_arabic_ocr(value)
        self.assertEqual(restored, "٢٠٢٦ سبتمبر ١١")
        self.assertTrue(restored.startswith("٢٠٢٦"))
        self.assertTrue(restored.endswith("١١"))

    def test_rtl_label_value_matching_preserves_value(self):
        data = {
            "page_lines": [
                {"content": "20261001 :ةروتافلا مقر", "page_number": 1},
            ]
        }
        result = search_page_lines(data, "رقم الفاتورة", target_page=1)
        self.assertEqual(result["value"], "20261001")

    def test_compound_arabic_label_does_not_match_generic_word(self):
        self.assertFalse(_line_label_match("إلى فاتورة", "رقم الفاتورة"))

    def test_split_reversed_arabic_words_match_as_a_compound_label(self):
        self.assertTrue(
            _line_label_match(
                "ضريبة القيمة المضافة",
                "٪١٥ %فاض ملا ةميقلا ۆبيرض",
            )
        )

    def test_translation_only_targets_descriptive_fields(self):
        self.assertTrue(_translation_requested("الوصف", {}, "خدمات هندسية"))
        self.assertTrue(_translation_requested("اسم المورد", {}, "شركة الطاقة"))
        self.assertFalse(_translation_requested("رقم الفاتورة", {}, "٢٠٢٦١٠٠١"))
        self.assertFalse(_translation_requested("VAT", {}, "٣١٠١٢٣٤٥٦٧٠٠٠٠٣"))
        self.assertFalse(_translation_requested(
            "الوصف", {"translate": False}, "خدمات هندسية"
        ))

    def test_numeric_kv_matching_rejects_text_value(self):
        data = {
            "key_value_pairs": [
                {
                    "key": {"content": "الإجمالي"},
                    "value": {"content": "وصف الخدمة"},
                    "confidence": 0.91,
                    "page_number": 1,
                },
                {
                    "key": {"content": "الإجمالي"},
                    "value": {"content": "56,062.50 SAR"},
                    "confidence": 0.82,
                    "page_number": 1,
                },
            ]
        }
        result = search_raw_kv(data, "الإجمالي", target_page=1)
        self.assertEqual(result["value"], "56,062.50 SAR")
        self.assertEqual(result["confidence"], 0.82)

    def test_auto_detected_status_uses_confidence_threshold(self):
        self.assertEqual(
            _validation_status("310123456700003", 0.218),
            ("Warning", "Low confidence"),
        )
        self.assertEqual(
            _validation_status(None, 0.95),
            ("Fail", "Required field missing"),
        )

    def test_typed_rules_preserve_legacy_page_mapping(self):
        rules = {
            "document_type": "arabic_tax_invoice",
            "pages": {
                "1": {
                    "fields": [
                        {
                            "name": "invoice_number",
                            "labels": {
                                "ar": "رقم الفاتورة",
                                "en": "Invoice Number",
                            },
                            "value_type": "invoice_number",
                            "required": True,
                            "translate": False,
                        }
                    ]
                },
                "2": ["الإجمالي"],
            },
        }
        self.assertEqual(
            parse_extraction_rules(rules),
            {1: ["invoice_number"], 2: ["الإجمالي"]},
        )
        spec = parse_extraction_rule_specs(rules)[1][0]
        self.assertEqual(spec["name"], "invoice_number")
        self.assertIn("رقم الفاتورة", spec["labels"])
        self.assertIn("Invoice Number", spec["labels"])
        self.assertEqual(spec["value_type"], "invoice_number")


if __name__ == "__main__":
    unittest.main()
