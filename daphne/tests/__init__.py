from hypothesis import HealthCheck, settings

settings.register_profile(
    'daphne',
    settings(suppress_health_check=[HealthCheck.too_slow]),
)
settings.load_profile('daphne')
