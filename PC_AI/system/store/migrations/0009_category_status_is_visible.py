from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("store", "0008_useraddress_websitesettings"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
IF COL_LENGTH('categories', 'status') IS NULL
BEGIN
    ALTER TABLE categories ADD status INT NOT NULL CONSTRAINT DF_categories_status DEFAULT 1;
END;

IF COL_LENGTH('categories', 'is_visible') IS NULL
BEGIN
    ALTER TABLE categories ADD is_visible BIT NOT NULL CONSTRAINT DF_categories_is_visible DEFAULT 1;
END;
""",
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
