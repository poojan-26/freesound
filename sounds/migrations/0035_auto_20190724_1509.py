# -*- coding: utf-8 -*-
# Generated by Django 1.11.20 on 2019-07-24 15:09
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sounds', '0034_make_latest_index_20190712_1616'),
    ]

    operations = [
        migrations.AlterField(
            model_name='sound',
            name='num_downloads',
            field=models.PositiveIntegerField(db_index=True, default=0),
        ),
    ]