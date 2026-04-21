import pandas as pd
from sklearn.metrics.pairwise import cosine_similarity
from django.db import connection


def run() -> int:
    with connection.cursor() as cursor:
        cursor.execute("SELECT id_users, id_products, score FROM user_item_scores")
        raw_rows = cursor.fetchall()

    df = pd.DataFrame(raw_rows, columns=["id_users", "id_products", "score"])
    if df.empty:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM item_similarity")
        return 0

    item_user_matrix = df.pivot_table(
        index="id_products",
        columns="id_users",
        values="score",
        aggfunc="sum",
        fill_value=0.0,
    )

    sim_matrix = cosine_similarity(item_user_matrix.values)
    product_ids = item_user_matrix.index.tolist()

    rows = []
    for i, p1 in enumerate(product_ids):
        for j, p2 in enumerate(product_ids):
            if p1 == p2:
                continue
            rows.append((int(p1), int(p2), float(sim_matrix[i, j])))

    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM item_similarity")
        if rows:
            cursor.fast_executemany = True
            cursor.executemany(
                "INSERT INTO item_similarity (product_1, product_2, similarity) VALUES (%s, %s, %s)",
                rows,
            )

    return len(rows)
