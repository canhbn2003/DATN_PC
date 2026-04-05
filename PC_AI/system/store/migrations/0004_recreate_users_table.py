from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("store", "0003_productimage_alter_cart_options_and_more"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
DECLARE @drop_fk_sql NVARCHAR(MAX) = N'';

SELECT @drop_fk_sql = @drop_fk_sql + N'ALTER TABLE ' + QUOTENAME(OBJECT_SCHEMA_NAME(parent_object_id)) + N'.' + QUOTENAME(OBJECT_NAME(parent_object_id)) + N' DROP CONSTRAINT ' + QUOTENAME(name) + N';'
FROM sys.foreign_keys
WHERE referenced_object_id = OBJECT_ID(N'users');

IF LEN(@drop_fk_sql) > 0
    EXEC sp_executesql @drop_fk_sql;

IF OBJECT_ID(N'users', N'U') IS NOT NULL
    DROP TABLE users;

CREATE TABLE users (
    id_users INT IDENTITY(1,1) PRIMARY KEY,
    name_users NVARCHAR(100),
    email NVARCHAR(100) UNIQUE,
    password NVARCHAR(255),
    role NVARCHAR(20),
    gender_users NVARCHAR(10),
    phone_users NVARCHAR(20),
    address_users NVARCHAR(255),
    created_at_users DATETIME DEFAULT GETDATE()
);
""",
            reverse_sql="""
IF OBJECT_ID(N'users', N'U') IS NOT NULL
    DROP TABLE users;
""",
        )
    ]
