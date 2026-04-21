from django.db import connection


def run() -> None:
    sql = """
    ;WITH behavior_scored AS (
        SELECT
            ub.id_users,
            ub.id_products,
            CASE ub.action_type_user_behavior
                WHEN 'view' THEN 1.0
                WHEN 'add_to_cart' THEN 4.0
                WHEN 'purchase' THEN 6.0
                ELSE 0.0
            END AS score_value
        FROM user_behavior ub
    ),
    agg AS (
        SELECT
            id_users,
            id_products,
            SUM(score_value) AS score
        FROM behavior_scored
        GROUP BY id_users, id_products
    )
    MERGE user_item_scores AS target
    USING agg AS src
    ON target.id_users = src.id_users
       AND target.id_products = src.id_products
    WHEN MATCHED THEN
        UPDATE SET target.score = src.score
    WHEN NOT MATCHED BY TARGET THEN
        INSERT (id_users, id_products, score)
        VALUES (src.id_users, src.id_products, src.score)
    WHEN NOT MATCHED BY SOURCE THEN
        DELETE;
    """

    with connection.cursor() as cursor:
        cursor.execute(sql)
