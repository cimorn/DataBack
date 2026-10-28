import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import './styles.css';
import './bulk-records.css';
import './motion.css';

createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>);
