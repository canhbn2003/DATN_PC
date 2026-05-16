import json
import os
import re

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from store.models import Product


GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1/models/{model}:generateContent"


def _resolve_gemini_models():
    raw_models = os.environ.get("GEMINI_MODEL") or str(getattr(settings, "GEMINI_MODEL", "") or "").strip()
    if raw_models:
        return [model.strip() for model in re.split(r"[\s,]+", raw_models) if model.strip()]

    return [
        "gemini-2.0-flash-lite-001",
        "gemini-2.0-flash-lite",
        "gemini-2.5-flash-lite",
        "gemini-2.0-flash-001",
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-2.5-pro",
    ]


def _list_gemini_models(api_key):
    try:
        response = requests.get(
            f"https://generativelanguage.googleapis.com/v1/models?key={api_key}",
            timeout=30,
        )
    except requests.RequestException as exc:
        return None, str(exc)

    if response.status_code >= 400:
        return None, response.text[:1000]

    try:
        payload = response.json()
    except Exception as exc:
        return None, str(exc)

    models = payload.get("models") or []
    available = []
    for item in models:
        name = item.get("name")
        methods = item.get("supportedGenerationMethods") or []
        if not name:
            continue
        if "generateContent" in methods:
            available.append(name.replace("models/", ""))

    return available, None


def _extract_json_payload(text):
    if not text:
        return ""

    text = str(text).strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1].strip()
    return text


class Command(BaseCommand):
    help = "Classify products by name only and output import-ready category JSON"

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Limit the number of products to classify (0 = all products)",
        )
        parser.add_argument(
            "--only-empty-category",
            action="store_true",
            help="Only classify products that do not have a category yet",
        )
        parser.add_argument(
            "--output",
            default="",
            help="Optional path to save the resulting JSON",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=8,
            help="Number of products to send to Gemini per request",
        )

    def handle(self, *args, **options):
        api_key = os.environ.get("GEMINI_API_KEY") or str(getattr(settings, "GEMINI_API_KEY", "") or "").strip()
        if not api_key:
            raise CommandError("GEMINI_API_KEY is not configured")

        queryset = Product.objects.select_related("id_categories").order_by("id_products")
        if options["only_empty_category"]:
            queryset = queryset.filter(id_categories__isnull=True)

        limit = int(options.get("limit") or 0)
        if limit > 0:
            queryset = queryset[:limit]

        products = list(queryset.values_list("id_products", "name_products"))
        if not products:
            payload = {"products": []}
            output_json = json.dumps(payload, ensure_ascii=False, indent=2)
            self._write_output(output_json, options.get("output") or "")
            self.stdout.write(output_json)
            return

        models = _resolve_gemini_models()
        allowed_categories = {"CPU", "GPU", "MAINBOARD", "RAM", "SSD", "PSU", "CASE", "COOLING", "UNKNOWN"}
        normalized_products = []
        batch_size = max(1, min(int(options.get("batch_size") or 8), 20))
        batches = [products[index : index + batch_size] for index in range(0, len(products), batch_size)]

        for batch_index, batch in enumerate(batches, start=1):
            batch_payload = self._classify_batch(api_key, models, batch, batch_index, len(batches), allowed_categories)
            normalized_products.extend(batch_payload)

        output_json = json.dumps({"products": normalized_products}, ensure_ascii=False, indent=2)
        self._write_output(output_json, options.get("output") or "")
        self.stdout.write(output_json)

    def _classify_batch(self, api_key, models, batch_products, batch_index, total_batches, allowed_categories):
        system_prompt = (
            "Ban la tro ly phan loai san pham. Chi duoc su dung TEN SAN PHAM tu DB, "
            "khong duoc ép tên, khong duoc tu suy doan theo synonym, brand hay mo ta. "
            "Tra ve DUY NHAT mot JSON hop le, khong markdown, khong giai thich, khong fenced code block. "
            "Schema bat buoc: {\"products\":[{\"product_id\":number,\"name\":string,\"category\":\"CPU\"|\"GPU\"|\"MAINBOARD\"|\"RAM\"|\"SSD\"|\"PSU\"|\"CASE\"|\"COOLING\"|\"UNKNOWN\"}]}."
        )

        product_lines = [f"ID:{product_id} | Ten:{(name or '').strip()}" for product_id, name in batch_products]
        user_prompt = (
            f"Phan loai {len(product_lines)} san pham trong lo {batch_index}/{total_batches}.\n"
            + "\n".join(product_lines)
            + "\n\nTra ve JSON dung schema, chi dung cac category hop le, va giu output rat ngan gon."
        )

        payload = {
            "contents": [{"parts": [{"text": f"{system_prompt}\n\n{user_prompt}"}]}],
            "generationConfig": {
                "temperature": 0.05,
                "maxOutputTokens": 1024,
            },
        }

        parsed = None
        last_error = None
        for model in models:
            endpoint = GEMINI_ENDPOINT.format(model=model)
            try:
                response = requests.post(
                    f"{endpoint}?key={api_key}",
                    json=payload,
                    timeout=70,
                )
            except requests.RequestException as exc:
                last_error = {"error": "Failed to call Gemini API", "details": str(exc)}
                continue

            if response.status_code == 404:
                last_error = {
                    "error": "Gemini API error",
                    "status": response.status_code,
                    "details": response.text[:1000],
                    "model": model,
                }
                continue

            if response.status_code in (429, 500, 502, 503, 504):
                last_error = {
                    "error": "Gemini API error",
                    "status": response.status_code,
                    "details": response.text[:1000],
                    "model": model,
                }
                continue

            if response.status_code >= 400:
                raise CommandError(f"Gemini API error {response.status_code}: {response.text[:1000]}")

            try:
                parsed = response.json()
            except Exception as exc:
                raise CommandError(f"Invalid JSON from Gemini: {exc}") from exc

            if parsed:
                break

        if not parsed:
            available_models, list_error = _list_gemini_models(api_key)
            error_payload = last_error or {"error": "Gemini API error", "details": "No available model."}
            if available_models:
                error_payload["available_models"] = available_models
            elif list_error:
                error_payload["available_models_error"] = list_error
            raise CommandError(json.dumps(error_payload, ensure_ascii=False))

        candidates = parsed.get("candidates")
        if not candidates or not isinstance(candidates, list):
            raise CommandError(f"Invalid response from Gemini: {parsed}")

        content = candidates[0].get("content", {})
        parts = content.get("parts", [])
        answer_text = ""
        if parts and isinstance(parts, list):
            answer_text = str(parts[0].get("text") or "").strip()

        if not answer_text:
            raise CommandError("Empty answer from Gemini")

        json_text = _extract_json_payload(answer_text)
        try:
            ai_payload = json.loads(json_text)
        except Exception:
            cleaned = re.sub(r",\s*([}\]])", r"\1", json_text).strip()
            try:
                ai_payload = json.loads(cleaned)
            except Exception as exc:
                raise CommandError(f"Invalid JSON from AI: {exc}. Raw: {answer_text[:1000]}") from exc

        products_payload = ai_payload.get("products") if isinstance(ai_payload, dict) else None
        if not isinstance(products_payload, list):
            raise CommandError(f"Invalid JSON schema: {ai_payload}")

        normalized_products = []
        for item in products_payload:
            if not isinstance(item, dict):
                continue

            try:
                product_id = int(item.get("product_id") or item.get("id"))
            except (TypeError, ValueError):
                continue

            name = str(item.get("name") or "").strip()
            category = str(item.get("category") or "UNKNOWN").strip().upper()
            if category not in allowed_categories:
                category = "UNKNOWN"

            normalized_products.append(
                {
                    "product_id": product_id,
                    "name": name,
                    "category": category,
                }
            )

        return normalized_products

    def _write_output(self, content, output_path):
        output_path = str(output_path or "").strip()
        if not output_path:
            return

        with open(output_path, "w", encoding="utf-8") as handle:
            handle.write(content)