idf_component_register(
    SRCS "main.c"
         "frame_client.c"
         "epd_driver.c"
         "fb2bpp.c"
         "config_store.c"
         "power.c"
         "led.c"
         "wifi_manager.c"
         "mqtt_parse.c"
         "framebuf.c"
         "provision_form.c"
         "provisioning.c"
         "splash.c"
         "lowbatt.c"
    INCLUDE_DIRS "."
    EMBED_FILES "assets/splash_setup.bin" "assets/splash_paired.bin" "assets/splash_lowbatt.bin"
    # esp_timer (provisioning.c) and mbedtls (frame_client.c's esp_crt_bundle.h,
    # for https server URLs) used to arrive transitively via the mqtt component;
    # dropping mqtt means both need naming explicitly.
    REQUIRES driver nvs_flash esp_wifi esp_netif esp_http_client esp_http_server
             json esp_adc lwip esp_hw_support esp_timer mbedtls)
