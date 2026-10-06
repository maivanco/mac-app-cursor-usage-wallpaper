#!/usr/bin/env swift
import AppKit
import Foundation

guard CommandLine.arguments.count > 1 else {
    fputs("Usage: set_wallpaper.swift /path/to/image.png\n", stderr)
    exit(1)
}

let path = CommandLine.arguments[1]
let url = URL(fileURLWithPath: path)

guard FileManager.default.fileExists(atPath: path) else {
    fputs("Wallpaper file not found: \(path)\n", stderr)
    exit(1)
}

var hadError = false
let screens = NSScreen.screens

if screens.isEmpty {
    fputs("No screens available.\n", stderr)
    exit(1)
}

for screen in screens {
    do {
        try NSWorkspace.shared.setDesktopImageURL(url, for: screen, options: [:])
    } catch {
        fputs("Failed to set wallpaper on \(screen.localizedName): \(error)\n", stderr)
        hadError = true
    }
}

exit(hadError ? 1 : 0)
