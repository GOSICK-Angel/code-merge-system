import React, { createContext, useContext, useState, useCallback } from "react";

export interface ThemeColors {
  primary: string;
  success: string;
  warning: string;
  danger: string;
  info: string;
  muted: string;
  accent: string;
  text: string;
  border: string;
  bg: string;
}

export interface Theme {
  name: "dark" | "light";
  colors: ThemeColors;
}

const darkTheme: Theme = {
  name: "dark",
  colors: {
    primary: "blue",
    success: "green",
    warning: "yellow",
    danger: "red",
    info: "cyan",
    muted: "gray",
    accent: "magenta",
    text: "white",
    border: "gray",
    bg: "black",
  },
};

const lightTheme: Theme = {
  name: "light",
  colors: {
    primary: "blueBright",
    success: "greenBright",
    warning: "yellowBright",
    danger: "redBright",
    info: "cyanBright",
    muted: "gray",
    accent: "magentaBright",
    text: "black",
    border: "gray",
    bg: "white",
  },
};

interface ThemeContextValue {
  theme: Theme;
  toggleTheme: () => void;
}

const ThemeCtx = createContext<ThemeContextValue>({
  theme: darkTheme,
  toggleTheme: () => {},
});

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setTheme] = useState<Theme>(darkTheme);

  const toggleTheme = useCallback(() => {
    setTheme((prev) => (prev.name === "dark" ? lightTheme : darkTheme));
  }, []);

  return (
    <ThemeCtx.Provider value={{ theme, toggleTheme }}>
      {children}
    </ThemeCtx.Provider>
  );
}

export function useTheme(): ThemeContextValue {
  return useContext(ThemeCtx);
}
