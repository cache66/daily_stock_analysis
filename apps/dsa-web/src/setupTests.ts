import '@testing-library/jest-dom';

class IntersectionObserverMock implements IntersectionObserver {
  readonly root = null;
  readonly rootMargin = '';
  readonly thresholds = [0];

  disconnect() {}

  observe() {}

  takeRecords(): IntersectionObserverEntry[] {
    return [];
  }

  unobserve() {}
}

Object.defineProperty(globalThis, 'IntersectionObserver', {
  writable: true,
  value: IntersectionObserverMock,
});

function createStorageMock(initialEntries?: Record<string, string>): Storage {
  const store = new Map(Object.entries(initialEntries ?? {}));

  return {
    get length() {
      return store.size;
    },
    clear() {
      store.clear();
    },
    getItem(key: string) {
      return store.has(key) ? store.get(key)! : null;
    },
    key(index: number) {
      return Array.from(store.keys())[index] ?? null;
    },
    removeItem(key: string) {
      store.delete(key);
    },
    setItem(key: string, value: string) {
      store.set(String(key), String(value));
    },
  };
}

const currentLocalStorage = (globalThis as { localStorage?: unknown }).localStorage;
const hasUsableLocalStorage =
  !!currentLocalStorage
  && typeof currentLocalStorage === 'object'
  && typeof (currentLocalStorage as Partial<Storage>).getItem === 'function'
  && typeof (currentLocalStorage as Partial<Storage>).setItem === 'function'
  && typeof (currentLocalStorage as Partial<Storage>).clear === 'function';

if (!hasUsableLocalStorage) {
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    writable: true,
    value: createStorageMock(),
  });
}
