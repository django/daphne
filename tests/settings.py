SECRET_KEY = 'cat'

INSTALLED_APPS = (
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.admin',
    'channels',
    'channels.delay',
    'tests',
)

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
    }
}

CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'asgiref.inmemory.ChannelLayer',
        'ROUTING': [],
    },
    'fake_channel': {
        'BACKEND': 'tests.test_management.FakeChannelLayer',
        'ROUTING': [],
    },
}

MIDDLEWARE_CLASSES = []
