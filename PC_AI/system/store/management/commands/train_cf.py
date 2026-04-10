from django.core.management.base import BaseCommand

from store.ai.pipelines import item_sim, scores, user_sim


class Command(BaseCommand):
    help = "Rebuild CF artifacts: user_item_scores, item_similarity, user_similarity"

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("[1/3] Building user_item_scores..."))
        scores.run()
        self.stdout.write(self.style.SUCCESS("Done user_item_scores"))

        self.stdout.write(self.style.NOTICE("[2/3] Building item_similarity..."))
        item_rows = item_sim.run()
        self.stdout.write(self.style.SUCCESS(f"Done item_similarity ({item_rows} rows)"))

        self.stdout.write(self.style.NOTICE("[3/3] Building user_similarity..."))
        user_rows = user_sim.run()
        self.stdout.write(self.style.SUCCESS(f"Done user_similarity ({user_rows} rows)"))

        self.stdout.write(self.style.SUCCESS("CF training pipeline completed."))
