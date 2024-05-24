
from django.db import models


class UserInfo(models.Model):
    name = models.CharField(max_length=100)
    contact = models.CharField(max_length=20)
    age = models.IntegerField()
    height = models.FloatField()
    weight = models.FloatField()
