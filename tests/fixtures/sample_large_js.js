/**
 * Sample JavaScript file for testing AST chunking.
 */

import { readFile } from 'fs/promises';
import path from 'path';

const MAX_ITEMS = 100;
const API_VERSION = 'v2';

class EventEmitter {
    constructor() {
        this._handlers = {};
    }

    on(event, handler) {
        if (!this._handlers[event]) {
            this._handlers[event] = [];
        }
        this._handlers[event].push(handler);
    }

    emit(event, data) {
        const handlers = this._handlers[event] || [];
        handlers.forEach(h => h(data));
    }

    off(event, handler) {
        if (!this._handlers[event]) return;
        this._handlers[event] = this._handlers[event].filter(h => h !== handler);
    }
}

function parseConfig(filePath) {
    const raw = readFile(filePath, 'utf-8');
    return JSON.parse(raw);
}

function validateSchema(data, schema) {
    const errors = [];
    for (const field of schema.required || []) {
        if (!(field in data)) {
            errors.push(`Missing required field: ${field}`);
        }
    }
    return errors;
}

async function fetchData(url, options = {}) {
    const response = await fetch(url, {
        ...options,
        headers: {
            'Content-Type': 'application/json',
            'X-API-Version': API_VERSION,
            ...options.headers,
        },
    });
    if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }
    return response.json();
}

export function formatDate(date) {
    return date.toISOString().split('T')[0];
}

export default class App {
    constructor(config) {
        this.config = config;
        this.emitter = new EventEmitter();
    }

    start() {
        console.log('App starting...');
        this.emitter.emit('start', { timestamp: Date.now() });
    }

    stop() {
        console.log('App stopping...');
        this.emitter.emit('stop', { timestamp: Date.now() });
    }
}
