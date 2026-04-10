from collections import defaultdict
from django.db import connection


def recommend_for_user(user_id: int, top_n: int = 10, seed_limit: int = 20):
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id_products, score
            FROM user_item_scores
            WHERE id_users = %s
            ORDER BY score DESC
            """,
            [user_id],
        )
        user_rows = cursor.fetchall()

    if not user_rows:
        return []

    interacted = {int(product_id): float(score) for product_id, score in user_rows}
    seed_products = list(interacted.keys())[:seed_limit]
    if not seed_products:
        return []

    placeholders = ",".join(["%s"] * len(seed_products))
    query = f"""
        SELECT product_1, product_2, similarity
        FROM item_similarity
        WHERE product_1 IN ({placeholders})
    """

    with connection.cursor() as cursor:
        cursor.execute(query, seed_products)
        sim_rows = cursor.fetchall()

    scores = defaultdict(float)
    for p1, p2, similarity in sim_rows:
        p1 = int(p1)
        p2 = int(p2)
        if p2 in interacted:
            continue
        scores[p2] += interacted.get(p1, 0.0) * float(similarity)

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [{"id_products": product_id, "score": score} for product_id, score in ranked[:top_n]]
