/**
 * FindBack Assistant - Client-side Controller
 * SEVONA LABS - SNS College of Technology
 * Problem Statement #34: Lost-and-Found Community Platform
 *
 * Provides grounded, asynchronous conversational interactions with FindBack Assistant.
 * Safe DOM rendering (no raw HTML injection) to prevent XSS.
 */

(function () {
  'use strict';

  // Helper: Escape text or build safe text nodes
  function createSafeText(text) {
    const span = document.createElement('span');
    span.textContent = text;
    return span;
  }

  // Helper: Format message lines safely (handling newlines without innerHTML)
  function appendFormattedText(container, rawText) {
    const lines = (rawText || '').split('\n');
    lines.forEach((line, idx) => {
      if (idx > 0) {
        container.appendChild(document.createElement('br'));
      }
      container.appendChild(document.createTextNode(line));
    });
  }

  // Setup a chat instance (works for both floating drawer and full-page)
  function initChatInterface(config) {
    const messagesContainer = document.getElementById(config.messagesId);
    const form = document.getElementById(config.formId);
    const input = document.getElementById(config.inputId);
    const sendBtn = document.getElementById(config.sendBtnId);
    const clearBtn = document.getElementById(config.clearBtnId);

    if (!messagesContainer || !form || !input || !sendBtn) {
      return;
    }

    let isProcessing = false;

    // Scroll to bottom of message view
    function scrollToBottom() {
      messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }

    // Append a message bubble to the container safely
    function appendMessage(role, text, actions) {
      const bubble = document.createElement('div');
      bubble.className = 'assistant-bubble ' + (role === 'user' ? 'assistant-msg-user' : 'assistant-msg-assistant');

      const author = document.createElement('div');
      author.className = 'assistant-bubble-author';
      author.textContent = role === 'user' ? 'You' : 'FindBack Assistant';
      bubble.appendChild(author);

      const contentDiv = document.createElement('div');
      contentDiv.className = 'assistant-bubble-text';
      appendFormattedText(contentDiv, text);
      bubble.appendChild(contentDiv);

      // Render action buttons safely if provided
      if (actions && Array.isArray(actions) && actions.length > 0) {
        const actionsContainer = document.createElement('div');
        actionsContainer.className = 'assistant-actions-container';

        actions.forEach(action => {
          if (!action || !action.url || !action.label) return;
          // Ensure URL is a safe internal relative path
          const url = String(action.url).trim();
          if (!url.startsWith('/')) return;

          const actionLink = document.createElement('a');
          actionLink.href = url;
          actionLink.className = 'assistant-action-btn';
          actionLink.textContent = action.label;
          actionsContainer.appendChild(actionLink);
        });

        if (actionsContainer.hasChildNodes()) {
          bubble.appendChild(actionsContainer);
        }
      }

      messagesContainer.appendChild(bubble);
      scrollToBottom();
      return bubble;
    }

    // Create loading bubble
    function showLoadingIndicator() {
      const loadingBubble = document.createElement('div');
      loadingBubble.className = 'assistant-bubble assistant-msg-assistant assistant-loading-bubble';
      loadingBubble.id = config.prefix + '-loading-bubble';

      const author = document.createElement('div');
      author.className = 'assistant-bubble-author';
      author.textContent = 'FindBack Assistant';
      loadingBubble.appendChild(author);

      const dots = document.createElement('div');
      dots.className = 'assistant-loading-dots';
      dots.innerHTML = '<span></span><span></span><span></span>';
      loadingBubble.appendChild(dots);

      messagesContainer.appendChild(loadingBubble);
      scrollToBottom();
    }

    // Remove loading bubble
    function removeLoadingIndicator() {
      const el = document.getElementById(config.prefix + '-loading-bubble');
      if (el) {
        el.remove();
      }
    }

    // Send message to Flask API
    async function sendMessage(textToSend) {
      const text = (textToSend || input.value || '').trim();
      if (!text || isProcessing) return;

      isProcessing = true;
      input.value = '';
      sendBtn.disabled = true;
      input.disabled = true;

      appendMessage('user', text);
      showLoadingIndicator();

      try {
        const response = await fetch('/api/assistant/chat', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Accept': 'application/json'
          },
          body: JSON.stringify({
            message: text,
            context: config.getContext ? config.getContext() : {}
          })
        });

        removeLoadingIndicator();

        if (!response.ok) {
          const errData = await response.json().catch(() => ({}));
          const errMsg = errData.error || 'The assistant encountered a server issue. Please try again.';
          appendMessage('assistant', errMsg);
        } else {
          const data = await response.json();
          let replyContent = data.reply;

          // Never display undefined, null, or the literal word "reply"
          if (!replyContent || String(replyContent).trim().toLowerCase() === 'reply') {
            replyContent = data.message || "Please describe the item you lost, or browse existing reports.";
          }

          if (data.status === 'success') {
            appendMessage('assistant', replyContent, data.actions);
          } else {
            appendMessage('assistant', data.error || replyContent || 'Unable to complete your request at this time.');
          }
        }
      } catch (err) {
        removeLoadingIndicator();
        appendMessage('assistant', 'Network communication error. Please check your connection or browse listings directly.');
      } finally {
        isProcessing = false;
        input.disabled = false;
        sendBtn.disabled = false;
        input.focus();
      }
    }

    // Reset conversation
    async function clearConversation() {
      if (isProcessing) return;
      try {
        await fetch('/api/assistant/clear', { method: 'POST' });
      } catch (e) {
        // Continue even if network fail
      }

      // Clear container and restore welcome message with actionable buttons
      messagesContainer.innerHTML = '';
      const initialBubble = document.createElement('div');
      initialBubble.className = 'assistant-bubble assistant-msg-assistant';

      const author = document.createElement('div');
      author.className = 'assistant-bubble-author';
      author.textContent = 'FindBack Assistant';
      initialBubble.appendChild(author);

      const content = document.createElement('div');
      content.className = 'assistant-bubble-text';
      content.textContent = 'Conversation reset. How can I help you with your lost or found items today?';
      initialBubble.appendChild(content);

      // Actionable starter prompts
      const starters = document.createElement('div');
      starters.className = 'assistant-starter-prompts';
      starters.innerHTML = `
        <a href="/items" class="assistant-starter-pill assistant-nav-pill">Find lost items</a>
        <a href="/report" class="assistant-starter-pill assistant-nav-pill">Report an item</a>
        <button class="assistant-starter-pill" type="button" data-prompt="Explain how Smart Match works and how potential matches are identified.">Explain Smart Match</button>
        <button class="assistant-starter-pill" type="button" data-prompt="How do I submit an ownership claim for an item?">How to claim</button>
      `;
      initialBubble.appendChild(starters);

      messagesContainer.appendChild(initialBubble);
      scrollToBottom();
    }

    // Event listeners
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      sendMessage();
    });

    input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
      }
    });

    if (clearBtn) {
      clearBtn.addEventListener('click', function () {
        clearConversation();
      });
    }

    // Starter prompts click handler within this container
    messagesContainer.addEventListener('click', function (e) {
      const pill = e.target.closest('.assistant-starter-pill');
      if (pill) {
        if (pill.tagName.toLowerCase() === 'a') {
          // Genuine navigation link - let browser navigate
          return;
        }
        if (pill.dataset.prompt) {
          e.preventDefault();
          sendMessage(pill.dataset.prompt);
        }
      }
    });
  }

  // Initialize UI once DOM is loaded
  document.addEventListener('DOMContentLoaded', function () {
    // 1. Setup Drawer Toggle & Controls
    const launcher = document.getElementById('assistant-launcher');
    const panel = document.getElementById('assistant-panel');
    const closeBtn = document.getElementById('assistant-close-btn');

    if (launcher && panel) {
      function openPanel() {
        panel.classList.remove('hidden');
        panel.setAttribute('aria-hidden', 'false');
        launcher.setAttribute('aria-expanded', 'true');
        const input = document.getElementById('assistant-input');
        if (input) input.focus();
      }

      function closePanel() {
        panel.classList.add('hidden');
        panel.setAttribute('aria-hidden', 'true');
        launcher.setAttribute('aria-expanded', 'false');
      }

      launcher.addEventListener('click', function () {
        if (panel.classList.contains('hidden')) {
          openPanel();
        } else {
          closePanel();
        }
      });

      if (closeBtn) {
        closeBtn.addEventListener('click', function () {
          closePanel();
        });
      }

      // Close on Escape key
      document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && !panel.classList.contains('hidden')) {
          closePanel();
        }
      });
    }

    // 2. Initialize Floating Drawer Chat
    initChatInterface({
      prefix: 'drawer',
      messagesId: 'assistant-messages',
      formId: 'assistant-form',
      inputId: 'assistant-input',
      sendBtnId: 'assistant-send-btn',
      clearBtnId: 'assistant-clear-btn'
    });

    // 3. Initialize Full-page Chat (if present)
    initChatInterface({
      prefix: 'fullpage',
      messagesId: 'fullpage-messages',
      formId: 'fullpage-form',
      inputId: 'fullpage-input',
      sendBtnId: 'fullpage-send-btn',
      clearBtnId: 'fullpage-clear-btn'
    });
  });
})();
