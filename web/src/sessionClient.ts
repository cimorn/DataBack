import { createSessionController } from './session';
import { publicRequest } from './request';

export const sessionController = createSessionController((path, body) => publicRequest(path, path === 'session' ? 'GET' : 'POST', body));
