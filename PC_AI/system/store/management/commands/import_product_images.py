import csv
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from store.models import Product, ProductImage, Discount, DiscountProduct, DiscountCategory, Promotion, PromotionProduct


class Command(BaseCommand):
    help = "Import product images from a CSV file into product_images table"

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            dest="file_path",
            default=None,
            help="Path to CSV file (defaults to product_images.csv or products_images.csv)",
        )

    def _resolve_csv_path(self, user_path):
        if user_path:
            path = Path(user_path)
            if not path.is_absolute():
                path = Path.cwd() / path
            return path

        default_candidates = [
            Path.cwd() / "product_images.csv",
            Path.cwd() / "products_images.csv",
        ]
        for candidate in default_candidates:
            if candidate.exists():
                return candidate

        return default_candidates[0]

    def handle(self, *args, **options):
        csv_path = self._resolve_csv_path(options["file_path"])

        if not csv_path.exists():
            raise CommandError(
                f"CSV file not found: {csv_path}. Create product_images.csv or pass --file."
            )

        created_count = 0
        skipped_count = 0
        error_count = 0

        with csv_path.open(newline="", encoding="utf-8") as csvfile:
            reader = csv.DictReader(csvfile)

            required_columns = {"id_products", "image_url"}
            if not reader.fieldnames or not required_columns.issubset(set(reader.fieldnames)):
                raise CommandError(
                    "CSV must contain headers: id_products,image_url"
                )

            for line_number, row in enumerate(reader, start=2):
                product_id = (row.get("id_products") or "").strip()
                image_url = (row.get("image_url") or "").strip()

                if not product_id or not image_url:
                    skipped_count += 1
                    self.stdout.write(
                        self.style.WARNING(
                            f"Line {line_number}: missing id_products or image_url, skipped"
                        )
                    )
                    continue

                try:
                    product = Product.objects.get(id_products=product_id)
                except Product.DoesNotExist:
                    error_count += 1
                    self.stdout.write(
                        self.style.ERROR(
                            f"Line {line_number}: Product ID {product_id} not found"
                        )
                    )
                    continue

                _, created = ProductImage.objects.get_or_create(
                    id_products=product,
                    image_url=image_url,
                )

                if created:
                    created_count += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"Line {line_number}: added image for product {product.id_products}"
                        )
                    )
                else:
                    skipped_count += 1
                    self.stdout.write(
                        self.style.WARNING(
                            f"Line {line_number}: duplicate image for product {product.id_products}, skipped"
                        )
                    )

        self.stdout.write(
            self.style.SUCCESS(
                f"Import finished. Created={created_count}, Skipped={skipped_count}, Errors={error_count}"
            )
        )
