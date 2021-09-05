# Tibero Database Backend Driver for Django
Forked from django Oracle database backend, switched to pyodbc connection

* Install django-tibero from github repository  
  ```code
  pip3 install git+https://github.com/cpyang/django-tibero.git  
  ```
* Modify mysite/settings.py with Tibero as default database  
  ```yaml
  DATABASES = {
      'default': {
          'ENGINE': "django_tibero",
          # Database DSN from ODBC.INI
          'NAME': "tibero",                 
          'USER': "tibero",
          'PASSWORD': "tmax",
          'OPTIONS': {
              'sql_trace': False,
              'unicode_results': False,
              'connection_timeout': 0,
              'dbshell', "iusql",
          },
      }
  }
  ```
