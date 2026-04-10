AI module for recommendation workflows.

Structure
- pipelines: jobs to build score/similarity tables.
- recommenders: runtime recommendation functions.
- sql: SQL scripts for batch score computation.

Suggested usage
1. Build user_item_scores from user_behavior.
2. Build item_similarity.
3. Build user_similarity.
4. Use recommenders in Django views/API.
