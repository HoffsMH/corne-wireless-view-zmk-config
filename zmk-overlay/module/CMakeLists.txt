# Only the half connected to the host knows the layer state.
if(CONFIG_ZMK_LAYER_SIGNAL AND (NOT CONFIG_ZMK_SPLIT OR CONFIG_ZMK_SPLIT_ROLE_CENTRAL))
  target_sources(app PRIVATE src/layer_signal.c)
endif()
