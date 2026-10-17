package com.example.store;

import java.util.HashMap;
import java.util.Map;

/** Keeps users in memory. */
public class Sample {
    private final Map<String, String> users = new HashMap<>();

    public Sample() {
        reset();
    }

    /** Find a user name by id. */
    public String find(String id) {
        return users.getOrDefault(id, defaultName());
    }

    private void reset() {
        users.clear();
    }

    private static String defaultName() {
        return "anonymous";
    }

    interface Listener {
        void onChange(String id);
    }
}
