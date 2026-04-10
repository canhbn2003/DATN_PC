import pandas as pd
from sklearn.metrics.pairwise import cosine_similarity
from django.db import connection


def run() -> int:
    with connection.cursor() as cursor:
        cursor.execute("SELECT id_users, id_products, score FROM user_item_scores")
        rows = cursor.fetchall()

    if not rows:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM user_similarity")
        return 0

    df = pd.DataFrame(rows, columns=["id_users", "id_products", "score"])

    user_item_matrix = df.pivot_table(
        index="id_users",
        columns="id_products",
        values="score",
        aggfunc="sum",
        fill_value=0.0,
    )

    sim_matrix = cosine_similarity(user_item_matrix.values)
    user_ids = user_item_matrix.index.tolist()

    rows_to_insert = []
    for i, u1 in enumerate(user_ids):
        for j, u2 in enumerate(user_ids):
            if u1 == u2:
                continue
            rows_to_insert.append((int(u1), int(u2), float(sim_matrix[i, j])))

    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM user_similarity")
        if rows_to_insert:
            cursor.fast_executemany = True
            cursor.executemany(
                "INSERT INTO user_similarity (user_1, user_2, similarity) VALUES (%s, %s, %s)",
                rows_to_insert,
            )

    return len(rows_to_insert)
