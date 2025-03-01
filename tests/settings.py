# pylint: disable=W0401, W0614
# flake8: noqa
from saleor.settings import *  # noqa

CELERY_TASK_ALWAYS_EAGER = True

SECRET_KEY = "+)(e#zz^oqaa-0svss8^)qx=)-p-0t5f@&0)odfo5i7@oyet@k"

ALLOWED_STOREFRONT_HOSTS = []

#Uncomment the default one
#ALLOWED_STOREFRONT_HOSTS = ["www.example.com"]

DEFAULT_CURRENCY = "USD"

TIME_ZONE = "America/Chicago"
LANGUAGE_CODE = "en"

ES_URL = None
SEARCH_BACKEND = "saleor.search.backends.postgresql"
INSTALLED_APPS = [a for a in INSTALLED_APPS if a != "django_elasticsearch_dsl"]

RECAPTCHA_PUBLIC_KEY = ""
RECAPTCHA_PRIVATE_KEY = ""

VATLAYER_ACCESS_KEY = ""

if "sqlite" in DATABASES["default"]["ENGINE"]:  # noqa
    DATABASES["default"]["TEST"] = {  # noqa
        "SERIALIZE": False,
        "NAME": ":memory:",
        "MIRROR": None,
    }

CHECKOUT_PAYMENT_GATEWAYS = {
    DUMMY: "Dummy gateway",
    BRAINTREE: "Braintree",
    RAZORPAY: "Razorpay",
    STRIPE: "Stripe",
}

COUNTRIES_ONLY = None

MEDIA_ROOT = None
MAX_CHECKOUT_LINE_QUANTITY = 50

USE_JSON_CONTENT = False

PASSWORD_HASHERS = ["tests.dummy_password_hasher.DummyHasher"]
EXTENSIONS_MANAGER = "saleor.extensions.manager.ExtensionsManager"

PLUGINS = []
