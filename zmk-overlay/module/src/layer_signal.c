/*
 * Report the full set of active layers to the host on every layer change:
 * press F24 plus F13+n for each active layer index n, then release them all.
 * Sending the whole set (not deltas) keeps the host in sync after a missed
 * report, a reconnect, or a keyboard reset.
 */

#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <dt-bindings/zmk/keys.h>
#include <zmk/event_manager.h>
#include <zmk/events/endpoint_changed.h>
#include <zmk/events/keycode_state_changed.h>
#include <zmk/events/layer_state_changed.h>
#include <zmk/keymap.h>

LOG_MODULE_DECLARE(zmk, CONFIG_ZMK_LOG_LEVEL);

#define FRAME_KEY F24

static const uint32_t layer_keys[] = {F13, F14, F15, F16, F17, F18, F19, F20, F21, F22, F23};
#define MAX_LAYERS ARRAY_SIZE(layer_keys)

static uint32_t pressed;
static bool frame_pressed;

static void set_key(uint32_t key, bool down) {
    raise_zmk_keycode_state_changed_from_encoded(key, down, k_uptime_get());
}

static void release_all(void) {
    for (int i = 0; i < MAX_LAYERS; i++) {
        if (pressed & BIT(i)) {
            set_key(layer_keys[i], false);
        }
    }
    pressed = 0;
    if (frame_pressed) {
        set_key(FRAME_KEY, false);
        frame_pressed = false;
    }
}

static void release_work_cb(struct k_work *work) { release_all(); }
static K_WORK_DELAYABLE_DEFINE(release_work, release_work_cb);

static void send_work_cb(struct k_work *work) {
    k_work_cancel_delayable(&release_work);
    release_all();

    set_key(FRAME_KEY, true);
    frame_pressed = true;
    for (int i = 0; i < MAX_LAYERS && i < ZMK_KEYMAP_LAYERS_LEN; i++) {
        zmk_keymap_layer_id_t id = zmk_keymap_layer_index_to_id(i);
        if (id != ZMK_KEYMAP_LAYER_ID_INVAL && zmk_keymap_layer_active(id)) {
            set_key(layer_keys[i], true);
            pressed |= BIT(i);
        }
    }
    k_work_reschedule(&release_work, K_MSEC(CONFIG_ZMK_LAYER_SIGNAL_TAP_MS));
}
static K_WORK_DELAYABLE_DEFINE(send_work, send_work_cb);

static int layer_signal_listener(const zmk_event_t *eh) {
    /* Coalesce bursts (e.g. &to) into one report, sent outside the event. */
    k_work_reschedule(&send_work, K_MSEC(1));
    return ZMK_EV_EVENT_BUBBLE;
}

ZMK_LISTENER(layer_signal, layer_signal_listener);
ZMK_SUBSCRIPTION(layer_signal, zmk_layer_state_changed);
ZMK_SUBSCRIPTION(layer_signal, zmk_endpoint_changed);
