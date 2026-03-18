import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { MantineProvider, createTheme } from "@mantine/core";
import { Notifications } from "@mantine/notifications";
import "@mantine/core/styles.css";
import "@mantine/notifications/styles.css";
import "./index.css";
import App from "./App.jsx";

const theme = createTheme({
  primaryColor: "hud-cyan",
  fontFamily: "'JetBrains Mono', monospace",
  fontFamilyMonospace: "'JetBrains Mono', monospace",
  defaultRadius: "xs",
  colors: {
    "hud-green": [
      "#e6fff2", "#b3ffda", "#80ffc2", "#4dffaa", "#1aff92",
      "#00ff88", "#00cc6d", "#009952", "#006637", "#00331c",
    ],
    "hud-amber": [
      "#fff8e6", "#ffe9b3", "#ffdb80", "#ffcc4d", "#ffbe1a",
      "#ffb800", "#cc9300", "#996e00", "#664900", "#332500",
    ],
    "hud-red": [
      "#ffe6e6", "#ffb3b3", "#ff8080", "#ff4d4d", "#ff3d3d",
      "#ff3d3d", "#cc3131", "#992525", "#661818", "#330c0c",
    ],
    "hud-cyan": [
      "#e6faff", "#b3f0ff", "#80e6ff", "#4ddbff", "#1ad1ff",
      "#00d4ff", "#00aacc", "#007f99", "#005566", "#002a33",
    ],
    "hud-violet": [
      "#f3e8ff", "#dbb8ff", "#c388ff", "#b388ff", "#a368ff",
      "#b388ff", "#8f6dcc", "#6b5299", "#483766", "#241b33",
    ],
  },
  components: {
    Card: {
      defaultProps: { shadow: "none" },
      styles: () => ({
        root: {
          backgroundColor: "var(--hud-bg-panel)",
          border: "1px solid var(--hud-border)",
          borderRadius: 2,
        },
      }),
    },
    Paper: {
      defaultProps: { shadow: "none" },
      styles: () => ({
        root: {
          backgroundColor: "var(--hud-bg-panel)",
          border: "1px solid var(--hud-border)",
          borderRadius: 2,
        },
      }),
    },
    Badge: {
      styles: () => ({
        root: {
          textTransform: "uppercase",
          letterSpacing: "0.5px",
          fontFamily: "'JetBrains Mono', monospace",
        },
      }),
    },
    Button: {
      styles: () => ({
        root: {
          textTransform: "uppercase",
          letterSpacing: "1px",
          fontFamily: "'JetBrains Mono', monospace",
          borderRadius: 2,
        },
      }),
    },
    Drawer: {
      styles: () => ({
        content: {
          backgroundColor: "var(--hud-bg-deep)",
        },
        header: {
          backgroundColor: "var(--hud-bg-deep)",
        },
      }),
    },
    Table: {
      styles: () => ({
        thead: {
          borderBottom: "1px solid var(--hud-border)",
        },
        th: {
          textTransform: "uppercase",
          letterSpacing: "1px",
          color: "var(--hud-text-dimmed)",
          fontSize: 11,
          borderBottom: "1px solid var(--hud-border)",
          boxShadow: "0 1px 0 rgba(0, 212, 255, 0.1)",
        },
        tr: {
          borderBottom: "1px solid var(--hud-border)",
        },
        td: {
          borderBottom: "1px solid var(--hud-border)",
        },
      }),
    },
    Tabs: {
      styles: () => ({
        tab: {
          textTransform: "uppercase",
          letterSpacing: "1px",
          fontFamily: "'JetBrains Mono', monospace",
          fontSize: 12,
          borderBottom: "2px solid transparent",
          "&[dataActive]": {
            borderBottomColor: "var(--hud-cyan)",
            boxShadow: "0 2px 8px rgba(0, 212, 255, 0.3)",
          },
        },
      }),
    },
    TextInput: {
      styles: () => ({
        input: {
          backgroundColor: "var(--hud-bg-surface)",
          borderColor: "var(--hud-border)",
          fontFamily: "'JetBrains Mono', monospace",
          "&:focus": {
            borderColor: "var(--hud-cyan)",
            boxShadow: "0 0 8px rgba(0, 212, 255, 0.2)",
          },
        },
      }),
    },
    Textarea: {
      styles: () => ({
        input: {
          backgroundColor: "var(--hud-bg-surface)",
          borderColor: "var(--hud-border)",
          fontFamily: "'JetBrains Mono', monospace",
          "&:focus": {
            borderColor: "var(--hud-cyan)",
            boxShadow: "0 0 8px rgba(0, 212, 255, 0.2)",
          },
        },
      }),
    },
    Select: {
      styles: () => ({
        input: {
          backgroundColor: "var(--hud-bg-surface)",
          borderColor: "var(--hud-border)",
          fontFamily: "'JetBrains Mono', monospace",
          "&:focus": {
            borderColor: "var(--hud-cyan)",
            boxShadow: "0 0 8px rgba(0, 212, 255, 0.2)",
          },
        },
        dropdown: {
          backgroundColor: "var(--hud-bg-panel)",
          borderColor: "var(--hud-border)",
        },
      }),
    },
    Alert: {
      styles: () => ({
        root: {
          borderRadius: 0,
          borderLeft: "3px solid",
        },
      }),
    },
    Code: {
      styles: () => ({
        root: {
          backgroundColor: "var(--hud-bg-surface)",
          color: "var(--hud-cyan)",
          fontFamily: "'JetBrains Mono', monospace",
        },
      }),
    },
    AppShell: {
      styles: () => ({
        header: {
          backgroundColor: "var(--hud-bg-panel)",
          borderBottom: "1px solid var(--hud-border)",
        },
        navbar: {
          backgroundColor: "var(--hud-bg-panel)",
          borderRight: "1px solid var(--hud-border)",
        },
        main: {
          backgroundColor: "var(--hud-bg-deep)",
        },
      }),
    },
  },
});

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <MantineProvider theme={theme} defaultColorScheme="dark">
      <Notifications position="top-right" />
      <App />
    </MantineProvider>
  </StrictMode>
);
