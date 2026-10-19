import '@testing-library/jest-dom/vitest';

import { cleanup, configure } from '@testing-library/react';
import { afterEach } from 'vitest';

import { tokens } from '../auth/tokens';

// jsdom installs its own AbortController, but fetch/Request come from Node, which rejects a
// jsdom AbortSignal ("Expected signal to be an instance of AbortSignal"). React Router passes
// one on every navigation. Tests don't abort requests, so drop the signal.
class TestRequest extends Request {
  constructor(input: RequestInfo | URL, init?: RequestInit) {
    if (init?.signal) {
      const { signal, ...rest } = init;
      super(input, rest);
    } else {
      super(input, init);
    }
  }
}
globalThis.Request = TestRequest;

// findBy*/waitFor give up after 1 s by default: too tight for a cold CI container doing
// login → redirect → /me. Real failures still fail, just 5 s later.
configure({ asyncUtilTimeout: 5000 });

afterEach(() => {
  cleanup();
  tokens.clear();
  localStorage.clear();
});
