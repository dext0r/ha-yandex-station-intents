def test_backward() -> None:
    from custom_components.yandex_station_intents import async_setup
    from custom_components.yandex_station_intents.config_flow import YandexSmartHomeIntentsFlowHandler
    from custom_components.yandex_station_intents.diagnostics import async_get_config_entry_diagnostics
    from custom_components.yandex_station_intents.media_player import YandexStationIntentMediaPlayer
    from custom_components.yandex_station_intents.yandex_intent import IntentManager
    from custom_components.yandex_station_intents.yandex_quasar import YandexQuasar
    from custom_components.yandex_station_intents.yandex_session import YandexSession

    for o in [
        async_setup,
        async_get_config_entry_diagnostics,
        YandexSmartHomeIntentsFlowHandler,
        YandexStationIntentMediaPlayer,
        IntentManager,
        YandexQuasar,
        YandexSession,
    ]:
        assert o
