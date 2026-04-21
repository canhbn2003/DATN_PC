from collections import defaultdict
from django.db import connection


def recommend_for_user(user_id: int, top_n: int = 10, top_k_users: int = 20):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id_products FROM user_item_scores WHERE id_users = %s",
            [user_id],
        )
        seen_rows = cursor.fetchall()
        seen_products = {int(row[0]) for row in seen_rows}

        cursor.execute(
            """
            SELECT user_2, similarity
            FROM user_similarity
            WHERE user_1 = %s
            ORDER BY similarity DESC
            """,
            [user_id],
        )
        similar_users = cursor.fetchall()

    if not similar_users:
        return []

    similar_users = similar_users[:top_k_users]
    similar_user_ids = [int(row[0]) for row in similar_users]
    similarity_map = {int(row[0]): float(row[1]) for row in similar_users}

    placeholders = ",".join(["%s"] * len(similar_user_ids))
    query = f"""
        SELECT id_users, id_products, score
        FROM user_item_scores
        WHERE id_users IN ({placeholders})
    """

    with connection.cursor() as cursor:
        cursor.execute(query, similar_user_ids)
        rows = cursor.fetchall()

    candidate_scores = defaultdict(float)
    for other_user, product_id, score in rows:
        other_user = int(other_user)
        product_id = int(product_id)
        if product_id in seen_products:
            continue
        candidate_scores[product_id] += similarity_map.get(other_user, 0.0) * float(score)

    ranked = sorted(candidate_scores.items(), key=lambda x: x[1], reverse=True)
    return [{"id_products": product_id, "score": score} for product_id, score in ranked[:top_n]]
