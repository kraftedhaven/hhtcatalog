/**
 * Hidden Haven Threads - Reseller Management Logic
 */

let currentImages = []; // Array of Base64 strings
let inventoryItems = JSON.parse(localStorage.getItem('ebbie_inventory') || '[]');

// eBay Category Map (translates display labels to eBay Category IDs)
const EBAY_CATEGORY_MAP = {
    "Outerwear > Jackets & Coats": "57988",
    "Tops > Vintage Graphic Tees": "15687",
    "Tops > Sweaters & Hoodies": "155183",
    "Bottoms > Washed Denim & Pants": "11481",
    "Accessories > Vintage Hats & Caps": "52357",
    "Sportswear > Archive & Vintage": "175759"
};

window.onload = function() {
    lucide.createIcons();
    renderTable();
    loadSavedCredentials();

    const dropZone = document.getElementById('drop-zone');
    if (dropZone) {
        dropZone.addEventListener('click', () => document.getElementById('file-input').click());
        dropZone.addEventListener('dragover', (e) => { e.preventDefault(); dropZone.classList.add('border-vintage-sand'); });
        dropZone.addEventListener('dragleave', () => dropZone.classList.remove('border-vintage-sand'));
        dropZone.addEventListener('drop', (e) => {
            e.preventDefault();
            dropZone.classList.remove('border-vintage-sand');
            if (e.dataTransfer.files.length) processFile(e.dataTransfer.files[0]);
        });
    }
};

function openSettingsModal() { document.getElementById('settings-modal').classList.remove('hidden'); }
function closeSettingsModal() { document.getElementById('settings-modal').classList.add('hidden'); }

function loadSavedCredentials() {
    // Priority 1: Check config.js (window.ENV)
    // Priority 2: Check localStorage (Manual UI entry)
    const gemini = window.ENV?.GEMINI_API_KEY || localStorage.getItem('cfg_gemini');
    const project = window.ENV?.APPWRITE_PROJECT_ID || localStorage.getItem('cfg_project');

    document.getElementById('cfg-gemini').value = gemini || '';
    document.getElementById('cfg-project').value = project || '';

    if (gemini && project) {
        const navStatus = document.getElementById('nav-key-status');
        if (navStatus) {
            navStatus.innerText = "Environment Connected";
            navStatus.classList.replace('text-vintage-sand', 'text-emerald-400');
        }
    }
}

function saveSettings(e) {
    if (e) e.preventDefault();
    localStorage.setItem('cfg_gemini', document.getElementById('cfg-gemini').value.trim());
    localStorage.setItem('cfg_project', document.getElementById('cfg-project').value.trim());
    showToast("Credentials override saved!");
    closeSettingsModal();
    loadSavedCredentials();
}

function handleFileSelect(e) { 
    if (e.target.files.length) {
        Array.from(e.target.files).forEach(file => processFile(file));
    }
}

function processFile(file) {
    const reader = new FileReader();
    reader.onload = function(e) {
        const base64 = e.target.result;
        currentImages.push(base64);
        renderImagePreviews();
    };
    reader.readAsDataURL(file);
}

function renderImagePreviews() {
    const grid = document.getElementById('image-grid');
    const container = document.getElementById('preview-container');
    const placeholder = document.getElementById('upload-placeholder');
    const btn = document.getElementById('generate-btn');

    if (currentImages.length > 0) {
        placeholder.classList.add('hidden');
        container.classList.remove('hidden');
        btn.disabled = false;
        
        grid.innerHTML = currentImages.map((src, idx) => `
            <div class="relative group aspect-square border border-vintage-border rounded-md overflow-hidden">
                <img src="${src}" class="w-full h-full object-cover">
                <button onclick="removeSingleImage(event, ${idx})" class="absolute top-0 right-0 bg-black/60 text-white p-0.5 opacity-0 group-hover:opacity-100 transition">
                    <i data-lucide="x" class="w-3 h-3"></i>
                </button>
            </div>
        `).join('');
        lucide.createIcons();
    }
}

function removeSingleImage(e, idx) {
    e.stopPropagation();
    currentImages.splice(idx, 1);
    if (currentImages.length === 0) {
        clearImages();
    } else {
        renderImagePreviews();
    }
}

function clearImages(e) {
    if (e) e.stopPropagation();
    currentImages = [];
    document.getElementById('image-grid').innerHTML = '';
    document.getElementById('preview-container').classList.add('hidden');
    document.getElementById('upload-placeholder').classList.remove('hidden');
    document.getElementById('generate-btn').disabled = true;
    document.getElementById('file-input').value = '';
}

async function generateItemDetails() {
    if (currentImages.length === 0) return showToast("Upload photos first!");
    
    // Check Config first, then LocalStorage
    const geminiKey = window.ENV?.GEMINI_API_KEY || localStorage.getItem('cfg_gemini');
    
    if (!geminiKey) {
        showToast("Missing Gemini Key in config.js or settings!");
        return openSettingsModal();
    }

    const btn = document.getElementById('generate-btn');
    btn.disabled = true;
    btn.innerText = `Analyzing ${currentImages.length} Photos...`;

    try {
        // Construct Multi-Part Vision Payload
        const imageParts = currentImages.map(img => {
            return {
                inlineData: {
                    mimeType: img.split(';')[0].split(':')[1],
                    data: img.split(',')[1]
                }
            };
        });

        const prompt = { text: `You are looking at a collection of ${currentImages.length} photos of a single vintage garment. Cross-reference the front, back, and tags to provide high-accuracy details. Return ONLY raw JSON with keys: "title", "price", "condition_id", "category", "description". Use a category from this list: ${Object.keys(EBAY_CATEGORY_MAP).join(', ')}` };

        const response = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent?key=${geminiKey}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                contents: [{ parts: [prompt, ...imageParts] }],
                generationConfig: { responseMimeType: "application/json" }
            })
        });

        const result = await response.json();
        const data = JSON.parse(result.candidates[0].content.parts[0].text);

        document.getElementById('item-title').value = data.title || '';
        document.getElementById('item-price').value = data.price || '30.00';
        document.getElementById('item-condition').value = data.condition_id || '3000';
        document.getElementById('item-category').value = data.category || 'Outerwear > Jackets & Coats';
        document.getElementById('item-desc').value = data.description || '';
        showToast("AI synthesized all photos!");
    } catch (err) {
        console.error(err);
        showToast("Vision Error. Try fewer photos or check Key.");
    } finally {
        btn.disabled = false;
        btn.innerText = "Auto-Extract Details with AI";
    }
}

async function saveToCatalog(e) {
    e.preventDefault();
    const item = {
        id: 'HHT-' + Date.now().toString().slice(-6),
        title: document.getElementById('item-title').value,
        price: parseFloat(document.getElementById('item-price').value).toFixed(2),
        quantity: document.getElementById('item-qty').value || '1',
        condition_id: document.getElementById('item-condition').value,
        category: document.getElementById('item-category').value,
        description: document.getElementById('item-desc').value,
        images: [...currentImages], // Save all photos
        date: new Date().toLocaleDateString()
    };

    // Appwrite v13 Sync
    const projectId = window.ENV?.APPWRITE_PROJECT_ID || localStorage.getItem('cfg_project');
    const endpoint = window.ENV?.APPWRITE_ENDPOINT || 'https://cloud.appwrite.io/v1';

    if (projectId) {
        try {
            const { Client, Databases, ID } = Appwrite;
            const client = new Client().setEndpoint(endpoint).setProject(projectId);
            const databases = new Databases(client);
            await databases.createDocument('default', 'inventory', ID.unique(), {
                title: item.title,
                price: item.price,
                category: item.category,
                description: item.description
            });
            showToast("Synced to Cloud!");
        } catch (err) {
            console.error("Appwrite failed", err);
        }
    }

    inventoryItems.unshift(item);
    localStorage.setItem('ebbie_inventory', JSON.stringify(inventoryItems));
    renderTable();
    showToast("Saved to Local Catalog!");
    document.getElementById('item-form').reset();
    clearImage();
}

function renderTable() {
    const body = document.getElementById('inventory-table-body');
    const countEl = document.getElementById('count');
    if (countEl) countEl.innerText = inventoryItems.length;

    if (inventoryItems.length === 0) {
        body.innerHTML = `<tr><td colspan="5" class="p-10 text-center text-vintage-accent font-serif">Empty Catalog.</td></tr>`;
        return;
    }

    body.innerHTML = inventoryItems.map((item, idx) => `
        <tr class="hover:bg-vintage-base/50 transition">
            <td class="p-4">
                <div class="flex -space-x-2">
                    ${(item.images || []).slice(0, 3).map(img => `<img src="${img}" class="w-8 h-8 object-cover rounded-full border-2 border-vintage-base shadow-sm">`).join('')}
                    ${(item.images || []).length > 3 ? `<div class="w-8 h-8 rounded-full bg-vintage-border text-[8px] flex items-center justify-center text-vintage-sand border-2 border-vintage-base">+${item.images.length - 3}</div>` : ''}
                </div>
            </td>
            <td class="p-4 font-medium text-vintage-cream">${item.title}</td>
            <td class="p-4 text-vintage-accent">${item.category}</td>
            <td class="p-4 text-vintage-sand font-serif">${item.price}</td>
            <td class="p-4">
                <button onclick="deleteItem(${idx})" class="text-vintage-accent hover:text-red-400"><i data-lucide="trash-2" class="w-4 h-4"></i></button>
            </td>
        </tr>
    `).join('');
    lucide.createIcons();
}

function deleteItem(idx) {
    inventoryItems.splice(idx, 1);
    localStorage.setItem('ebbie_inventory', JSON.stringify(inventoryItems));
    renderTable();
}

function downloadEbayCSV() {
    const headers = ["*Action(SiteID=US|Country=US|Currency=USD|Version=745)", "CustomLabel", "*Category", "*Title", "*Description", "*Quantity", "*Format", "*StartPrice", "*ConditionID", "*Duration"];
    const rows = inventoryItems.map(item => [
        "Add", `"${item.id}"`, `"${EBAY_CATEGORY_MAP[item.category] || '11450'}"`,
        `"${item.title.replace(/"/g, '""')}"`, `"${item.description.replace(/"/g, '""')}"`,
        item.quantity, "FixedPrice", item.price, item.condition_id, "GTC"
    ]);
    downloadCSV([headers, ...rows], "eBay_Bulk_Upload.csv");
}



function downloadCSV(rows, filename) {
    const csvContent = rows.map(r => r.join(",")).join("\n");
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = filename;
    link.click();
}

function showToast(msg) {
    const toast = document.getElementById('toast');
    document.getElementById('toast-text').innerText = msg;
    toast.classList.remove('hidden');
    setTimeout(() => toast.classList.add('hidden'), 3000);
}