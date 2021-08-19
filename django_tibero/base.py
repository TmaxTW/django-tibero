"""
Oracle database backend for Django.

Requires cx_Oracle: http://cx-oracle.sourceforge.net/
"""
from __future__ import unicode_literals

import datetime
import decimal
import os
import platform
import sys
import warnings
import six

from django.conf import settings
from django.db import utils
from django.db.backends.base.base import BaseDatabaseWrapper
from django.db.backends.base.validation import BaseDatabaseValidation
from django.utils import timezone
#from django.utils.deprecation import RemovedInDjango30Warning
from django.utils.duration import duration_string
from django.utils.encoding import force_bytes, force_text
from django.utils.functional import cached_property

def encode_connection_string(fields):
    return ';'.join('%s=%s' % (k, encode_value(v)) for k, v in fields.items())

def encode_value(v):
    if ';' in v or v.strip(' ').startswith('{'):
        return '{%s}' % (v.replace('}', '}}'),)
    return v

def _setup_environment(environ):
    # Cygwin requires some special voodoo to set the environment variables
    # properly so that Oracle will see them.
    if platform.system().upper().startswith('CYGWIN'):
        try:
            import ctypes
        except ImportError as e:
            from django.core.exceptions import ImproperlyConfigured
            raise ImproperlyConfigured("Error loading ctypes: %s; "
                                       "the Oracle backend requires ctypes to "
                                       "operate correctly under Cygwin." % e)
        kernel32 = ctypes.CDLL('kernel32')
        for name, value in environ:
            kernel32.SetEnvironmentVariableA(name, value)
    else:
        os.environ.update(environ)

_setup_environment([
    # Oracle takes client-side character set encoding from the environment.
    ('TB_NLS_LANG', 'UTF8'),
    # This prevents unicode from getting mangled by getting encoded into the
    # potentially non-unicode database character set.
    ('TBCLI_WCHAR_TYPE', 'UCS2'),
    #('ORA_NCHAR_LITERAL_REPLACE', 'TRUE'),
])


try:
    import pyodbc as Database
except ImportError as e:
    from django.core.exceptions import ImproperlyConfigured
    raise ImproperlyConfigured("Error loading cx_Oracle module: %s" % e)

# Some of these import cx_Oracle, so import them after checking if it's installed.
from .client import DatabaseClient                          # NOQA isort:skip
from .creation import DatabaseCreation                      # NOQA isort:skip
from .features import DatabaseFeatures                      # NOQA isort:skip
from .introspection import DatabaseIntrospection            # NOQA isort:skip
from .operations import DatabaseOperations                  # NOQA isort:skip
from .schema import DatabaseSchemaEditor                    # NOQA isort:skip
from .utils import Oracle_datetime, convert_unicode         # NOQA isort:skip

DatabaseError = Database.DatabaseError
IntegrityError = Database.IntegrityError


class _UninitializedOperatorsDescriptor(object):

    def __get__(self, instance, cls=None):
        # If connection.operators is looked up before a connection has been
        # created, transparently initialize connection.operators to avert an
        # AttributeError.
        if instance is None:
            raise AttributeError("operators not available as class attribute")
        # Creating a cursor will initialize the operators.
        instance.cursor().close()
        return instance.__dict__['operators']


class DatabaseWrapper(BaseDatabaseWrapper):
    vendor = 'oracle'
    # This dictionary maps Field objects to their associated Oracle column
    # types, as strings. Column-type strings can contain format strings; they'll
    # be interpolated against the values of Field.__dict__ before being output.
    # If a column type is set to None, it won't be included in the output.
    #
    # Any format strings starting with "qn_" are quoted before being used in the
    # output (the "qn_" prefix is stripped before the lookup is performed.
    data_types = {
        'AutoField': 'NUMBER(11)',
        'BigAutoField': 'NUMBER(19)',
        'BinaryField': 'BLOB',
        'BooleanField': 'NUMBER(1)',
        'CharField': 'NVARCHAR2(%(max_length)s)',
        'CommaSeparatedIntegerField': 'VARCHAR2(%(max_length)s)',
        'DateField': 'DATE',
        'DateTimeField': 'TIMESTAMP',
        'DecimalField': 'NUMBER(%(max_digits)s, %(decimal_places)s)',
        'DurationField': 'INTERVAL DAY(9) TO SECOND(6)',
        'FileField': 'NVARCHAR2(%(max_length)s)',
        'FilePathField': 'NVARCHAR2(%(max_length)s)',
        'FloatField': 'DOUBLE PRECISION',
        'IntegerField': 'NUMBER(11)',
        'BigIntegerField': 'NUMBER(19)',
        'IPAddressField': 'VARCHAR2(15)',
        'GenericIPAddressField': 'VARCHAR2(39)',
        'NullBooleanField': 'NUMBER(1)',
        'OneToOneField': 'NUMBER(11)',
        'PositiveIntegerField': 'NUMBER(11)',
        'PositiveSmallIntegerField': 'NUMBER(11)',
        'SlugField': 'NVARCHAR2(%(max_length)s)',
        'SmallIntegerField': 'NUMBER(11)',
        'TextField': 'NCLOB',
        'TimeField': 'TIMESTAMP',
        'URLField': 'VARCHAR2(%(max_length)s)',
        'UUIDField': 'VARCHAR2(32)',
    }
    data_type_check_constraints = {
        'BooleanField': '%(qn_column)s IN (0,1)',
        'NullBooleanField': '(%(qn_column)s IN (0,1)) OR (%(qn_column)s IS NULL)',
        'PositiveIntegerField': '%(qn_column)s >= 0',
        'PositiveSmallIntegerField': '%(qn_column)s >= 0',
    }

    operators = _UninitializedOperatorsDescriptor()

    _standard_operators = {
        'exact': '= %s',
        'iexact': '= UPPER(%s)',
        'contains': "LIKE TRANSLATE(%s USING NCHAR_CS) ESCAPE TRANSLATE('\\' USING NCHAR_CS)",
        'icontains': "LIKE UPPER(TRANSLATE(%s USING NCHAR_CS)) ESCAPE TRANSLATE('\\' USING NCHAR_CS)",
        'gt': '> %s',
        'gte': '>= %s',
        'lt': '< %s',
        'lte': '<= %s',
        'startswith': "LIKE TRANSLATE(%s USING NCHAR_CS) ESCAPE TRANSLATE('\\' USING NCHAR_CS)",
        'endswith': "LIKE TRANSLATE(%s USING NCHAR_CS) ESCAPE TRANSLATE('\\' USING NCHAR_CS)",
        'istartswith': "LIKE UPPER(TRANSLATE(%s USING NCHAR_CS)) ESCAPE TRANSLATE('\\' USING NCHAR_CS)",
        'iendswith': "LIKE UPPER(TRANSLATE(%s USING NCHAR_CS)) ESCAPE TRANSLATE('\\' USING NCHAR_CS)",
    }

    _likec_operators = _standard_operators.copy()
    _likec_operators.update({
        'contains': "LIKEC %s ESCAPE '\\'",
        'icontains': "LIKEC UPPER(%s) ESCAPE '\\'",
        'startswith': "LIKEC %s ESCAPE '\\'",
        'endswith': "LIKEC %s ESCAPE '\\'",
        'istartswith': "LIKEC UPPER(%s) ESCAPE '\\'",
        'iendswith': "LIKEC UPPER(%s) ESCAPE '\\'",
    })

    # The patterns below are used to generate SQL pattern lookup clauses when
    # the right-hand side of the lookup isn't a raw string (it might be an expression
    # or the result of a bilateral transformation).
    # In those cases, special characters for LIKE operators (e.g. \, *, _) should be
    # escaped on database side.
    #
    # Note: we use str.format() here for readability as '%' is used as a wildcard for
    # the LIKE operator.
    pattern_esc = r"REPLACE(REPLACE(REPLACE({}, '\', '\\'), '%%', '\%%'), '_', '\_')"
    _pattern_ops = {
        'contains': "'%%' || {} || '%%'",
        'icontains': "'%%' || UPPER({}) || '%%'",
        'startswith': "{} || '%%'",
        'istartswith': "UPPER({}) || '%%'",
        'endswith': "'%%' || {}",
        'iendswith': "'%%' || UPPER({})",
    }

    _standard_pattern_ops = {k: "LIKE TRANSLATE( " + v + " USING NCHAR_CS)"
                                " ESCAPE TRANSLATE('\\' USING NCHAR_CS)"
                             for k, v in _pattern_ops.items()}
    _likec_pattern_ops = {k: "LIKEC " + v + " ESCAPE '\\'"
                          for k, v in _pattern_ops.items()}

    Database = Database
    SchemaEditorClass = DatabaseSchemaEditor

    def __init__(self, *args, **kwargs):
        self.client_class = DatabaseClient
        self.creation_class = DatabaseCreation
        self.features_class = DatabaseFeatures
        self.introspection_class = DatabaseIntrospection
        self.ops_class = DatabaseOperations
        self.validation_class = BaseDatabaseValidation
        use_returning_into = False
        self.features_class.can_return_id_from_insert = use_returning_into
        super(DatabaseWrapper, self).__init__(*args, **kwargs)


    def get_connection_params(self):
        settings_dict = self.settings_dict
        if settings_dict['NAME'] == '':
            from django.core.exception import ImproperlyConfigured
            raise ImproperlyConfigured("improper configured")
        conn_params = settings_dict.copy()
        if conn_params['NAME'] is None:
            conn_params['NAME'] = 'master' 
        return conn_params

    def get_new_connection(self, conn_params):
        database = conn_params['NAME']
        host = conn_params.get('HOST', 'localhost')
        user = conn_params.get('USER', None)
        password = conn_params.get('PASSWORD', None)
        port = conn_params.get('PORT', None)

        options = conn_params.get('OPTIONS', {})
        dsn = options.get('dsn', None)
        cstr_parts = {}
        cstr_parts['DSN'] = dsn if dsn is not None else database
        cstr_parts['UID'] = user
        cstr_parts['PWD'] = password
        cstr_parts['DATABASE'] = database

        connstr = encode_connection_string(cstr_parts)

        unicode_results = options.get('unicode_results', False)
        timeout = options.get('connection_timeout', 0)

        conn = None
        conn = Database.connect(connstr, unicode_results=unicode_results,
                                timeout=timeout)
        return conn 

    def init_connection_state(self):
        cursor = self.create_cursor()
        # Set the territory first. The territory overrides NLS_DATE_FORMAT
        # and NLS_TIMESTAMP_FORMAT to the territory default. When all of
        # these are set in single statement it isn't clear what is supposed
        # to happen.
#        cursor.execute("ALTER SESSION SET NLS_TERRITORY = 'AMERICA'")
        # Set Oracle date to ANSI date format.  This only needs to execute
        # once when we create a new connection. We also set the Territory
        # to 'AMERICA' which forces Sunday to evaluate to a '1' in
        # TO_CHAR().
        cursor.execute(
            "ALTER SESSION SET NLS_DATE_FORMAT = 'YYYY-MM-DD HH24:MI:SS'"
            " NLS_TIMESTAMP_FORMAT = 'YYYY-MM-DD HH24:MI:SS.FF'" +
            (" TIME_ZONE = 'UTC'" if settings.USE_TZ else '')
        )
        cursor.close()
        if 'operators' not in self.__dict__:
            # Ticket #14149: Check whether our LIKE implementation will
            # work for this connection or we need to fall back on LIKEC.
            # This check is performed only once per DatabaseWrapper
            # instance per thread, since subsequent connections will use
            # the same settings.
            cursor = self.create_cursor()
            try:
                cursor.execute("SELECT 1 FROM DUAL WHERE DUMMY %s"
                               % self._standard_operators['contains'],
                               ['X'])
            except DatabaseError:
                self.operators = self._likec_operators
                self.pattern_ops = self._likec_pattern_ops
            else:
                self.operators = self._standard_operators
                self.pattern_ops = self._standard_pattern_ops
            cursor.close()

        try:
            self.connection.stmtcachesize = 20
        except AttributeError:
            # Django docs specify cx_Oracle version 4.3.1 or higher, but
            # stmtcachesize is available only in 4.3.2 and up.
            pass
        # Ensure all changes are preserved even when AUTOCOMMIT is False.
        if not self.get_autocommit():
            self.commit()

    def create_cursor(self, name=None):
        return FormatStylePlaceholderCursor(self.connection)

    def _commit(self):
        if self.connection is not None:
            try:
                return self.connection.commit()
            except Database.DatabaseError as e:
                # cx_Oracle 5.0.4 raises a cx_Oracle.DatabaseError exception
                # with the following attributes and values:
                #  code = 2091
                #  message = 'ORA-02091: transaction rolled back
                #            'ORA-02291: integrity constraint (TEST_DJANGOTEST.SYS
                #               _C00102056) violated - parent key not found'
                # We convert that particular case to our IntegrityError exception
                x = e.args[0]
                if hasattr(x, 'code') and hasattr(x, 'message') \
                   and x.code == 2091 and 'ORA-02291' in x.message:
                    six.reraise(utils.IntegrityError, utils.IntegrityError(*tuple(e.args)), sys.exc_info()[2])
                raise

    # Oracle doesn't support releasing savepoints. But we fake them when query
    # logging is enabled to keep query counts consistent with other backends.
    def _savepoint_commit(self, sid):
        if self.queries_logged:
            self.queries_log.append({
                'sql': '-- RELEASE SAVEPOINT %s (faked)' % self.ops.quote_name(sid),
                'time': '0.000',
            })

    def _set_autocommit(self, autocommit):
        with self.wrap_database_errors:
            self.connection.autocommit = autocommit

    def check_constraints(self, table_names=None):
        """
        To check constraints, we set constraints to immediate. Then, when, we're done we must ensure they
        are returned to deferred.
        """
        pass
        #self.cursor().execute('SET CONSTRAINTS ALL IMMEDIATE')
        #self.cursor().execute('SET CONSTRAINTS ALL DEFERRED')

    def is_usable(self):
        try:
            self.create_cursor().execute("select 1 from dual")
        except Database.Error:
            return False
        else:
            return True

    @cached_property
    def oracle_full_version(self):
        with self.temporary_connection():
            return self.connection.version

    @cached_property
    def oracle_version(self):
        try:
            return int(self.oracle_full_version.split('.')[0])
        except ValueError:
            return None


class OracleParam(object):
    """
    Wrapper object for formatting parameters for Oracle. If the string
    representation of the value is large enough (greater than 4000 characters)
    the input size needs to be set as CLOB. Alternatively, if the parameter
    has an `input_size` attribute, then the value of the `input_size` attribute
    will be used instead. Otherwise, no input size will be set for the
    parameter when executing the query.
    """

    def __init__(self, param, cursor, strings_only=False):
        # With raw SQL queries, datetimes can reach this function
        # without being converted by DateTimeField.get_db_prep_value.
        if settings.USE_TZ and (isinstance(param, datetime.datetime) and
                                not isinstance(param, Oracle_datetime)):
            if timezone.is_aware(param):
                warnings.warn(
                    "The Oracle database adapter received an aware datetime (%s), "
                    "probably from cursor.execute(). Update your code to pass a "
                    "naive datetime in the database connection's time zone (UTC by "
                    "default).", RemovedInDjango20Warning)
                param = param.astimezone(timezone.utc).replace(tzinfo=None)
            param = Oracle_datetime.from_datetime(param)

        if isinstance(param, datetime.timedelta):
            param = duration_string(param)
            if ' ' not in param:
                param = '0 ' + param

        string_size = 0
        # Oracle doesn't recognize True and False correctly in Python 3.
        # The conversion done below works both in 2 and 3.
        if param is True:
            param = 1
        elif param is False:
            param = 0
        if hasattr(param, 'bind_parameter'):
            self.force_bytes = param.bind_parameter(cursor)
        elif isinstance(param, Database.Binary):
            self.force_bytes = param
        else:
            # To transmit to the database, we need Unicode if supported
            # To get size right, we must consider bytes.
            self.force_bytes = convert_unicode(param, cursor.charset, strings_only)
            if isinstance(self.force_bytes, six.string_types):
                # We could optimize by only converting up to 4000 bytes here
                string_size = len(force_bytes(param, cursor.charset, strings_only))
        if hasattr(param, 'input_size'):
            # If parameter has `input_size` attribute, use that.
            self.input_size = param.input_size
        elif string_size > 4000:
            # Mark any string param greater than 4000 characters as a CLOB.
            self.input_size = Database.CLOB
        else:
            self.input_size = None


class VariableWrapper(object):
    """
    An adapter class for cursor variables that prevents the wrapped object
    from being converted into a string when used to instantiate an OracleParam.
    This can be used generally for any other object that should be passed into
    Cursor.execute as-is.
    """

    def __init__(self, var):
        self.var = var

    def bind_parameter(self, cursor):
        return self.var

    def __getattr__(self, key):
        return getattr(self.var, key)

    def __setattr__(self, key, value):
        if key == 'var':
            self.__dict__[key] = value
        else:
            setattr(self.var, key, value)


class FormatStylePlaceholderCursor(object):
    """
    Django uses "format" (e.g. '%s') style placeholders, but Oracle uses ":var"
    style. This fixes it -- but note that if you want to use a literal "%s" in
    a query, you'll need to use "%%s".

    We also do automatic conversion between Unicode on the Python side and
    UTF-8 -- for talking to Oracle -- in here.
    """
    charset = 'utf-8'

    def __init__(self, connection):
        self.active = True
        self.cursor = connection.cursor()
        # Necessary to retrieve decimal values without rounding error.
#        self.cursor.numbersAsStrings = True
        # Default arraysize of 1 is highly sub-optimal.
        self.cursor.arraysize = 100
        self.connection = connection
        self.last_sql = ''
        self.last_params = ()

    def format_params(self, params):
        fp = []
        if params is not None:
            for p in params:
                if isinstance(p, datetime.timedelta):
                    p = duration_string(p)
                    if ' ' not in p:
                        p = '0 ' + p
                fp.append(p)
        return tuple(fp)

    def format_query(self, query, params):
        if params is not None:
            query = query % tuple('?' * len(params))
        return query

    def execute(self, query, params=None):
        self.last_sql = query
        query = self.format_query(query, params)
        params = self.format_params(params)
        self.last_params = params
        try:
            return self.cursor.execute(query, params)
        except Database.DatabaseError as e:
            # cx_Oracle <= 4.4.0 wrongly raises a DatabaseError for ORA-01400.
            raise

    def executemany(self, query, params_list=()):
        if not params_list:
            return None
        # uniform treatment for sequences and iterables
        raw_pll = [p for p in params_list]
        query = self.format_query(query, raw_pll[0])
        params_list = [self.format_params(p) for p in raw_pll]

        try:
            return self.cursor.executemany(query, params_list)
        except Database.DatabaseError as e:
            # cx_Oracle <= 4.4.0 wrongly raises a DatabaseError for ORA-01400.
            raise

    def fetchone(self):
        row = self.cursor.fetchone()
        if row is None:
            return row
        return _rowfactory(row, self.cursor)

    def fetchmany(self, size=None):
        if size is None:
            size = self.arraysize
        return tuple(_rowfactory(r, self.cursor) for r in self.cursor.fetchmany(size))

    def fetchall(self):
        return tuple(_rowfactory(r, self.cursor) for r in self.cursor.fetchall())

    def close(self):
        if self.active:
            self.active = False
            self.cursor.close()

    def var(self, *args):
        return VariableWrapper(self.cursor.var(*args))

    def arrayvar(self, *args):
        return VariableWrapper(self.cursor.arrayvar(*args))

    def __getattr__(self, attr):
        if attr in self.__dict__:
            return self.__dict__[attr]
        else:
            return getattr(self.cursor, attr)

    def __iter__(self):
        return CursorIterator(self.cursor)


class CursorIterator(six.Iterator):
    """
    Cursor iterator wrapper that invokes our custom row factory.
    """

    def __init__(self, cursor):
        self.cursor = cursor
        self.iter = iter(cursor)

    def __iter__(self):
        return self

    def __next__(self):
        return _rowfactory(next(self.iter), self.cursor)


def _rowfactory(row, cursor):
    # Cast numeric values as the appropriate Python type based upon the
    # cursor description, and convert strings to unicode.
    casted = []
    for value, desc in zip(row, cursor.description):
        if value is not None and desc[1] is Database.NUMBER:
            precision, scale = desc[4:6]
            if scale == -127:
                if precision == 0:
                    # NUMBER column: decimal-precision floating point
                    # This will normally be an integer from a sequence,
                    # but it could be a decimal value.
                    if '.' in value:
                        value = decimal.Decimal(value)
                    else:
                        value = int(value)
                else:
                    # FLOAT column: binary-precision floating point.
                    # This comes from FloatField columns.
                    value = float(value)
            elif precision > 0:
                # NUMBER(p,s) column: decimal-precision fixed point.
                # This comes from IntField and DecimalField columns.
                if scale == 0:
                    value = int(value)
                else:
                    value = decimal.Decimal(value)
            elif '.' in value:
                # No type information. This normally comes from a
                # mathematical expression in the SELECT list. Guess int
                # or Decimal based on whether it has a decimal point.
                value = decimal.Decimal(value)
            else:
                value = int(value)
        elif desc[1] in (Database.STRING,):
            value = to_unicode(value)
        casted.append(value)
    return tuple(casted)


def to_unicode(s):
    """
    Convert strings to Unicode objects (and return all other data types
    unchanged).
    """
    if isinstance(s, six.string_types):
        return force_text(s)
    return s
