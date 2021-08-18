# django-tibero
Tibero Database Backend Driver for Django

## Use django-tibero from github repository
* pip3 install git+https://github.com/cpyang/django-tibero.git  
* Modify mysite/settings.py with Tibero as default database  
    DATABASES = {
        'default': {
            'ENGINE': "django_tibero",
            'HOST': "127.0.0.1,8629",
            'USER': "tibero",
            'PASSWORD': "tmax",
            'NAME': "tibero",
            'OPTIONS': {
                'dsn': "tibero",
                'driver': "Tibero",
                'host_is_server': True,
                'dbms_type': "tibero",
            },
        }
    }
