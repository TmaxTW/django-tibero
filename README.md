# django-tibero
Tibero Database Backend Driver for Django

## Use django-tibero from github repository
* pip3 install git+https://github.com/cpyang/django-tibero.git  
* Modify mysite/settings.py with Tibero as default database  
"""yaml
    DATABASES = {
        'default': {
            'ENGINE': "django_tibero",
            'NAME': "tibero",
            'HOST': "127.0.0.1,8629",
            'USER': "tibero",
            'PASSWORD': "tmax",
            'OPTIONS': {
                'dsn': "tibero",
            },
        }
    }
"""
