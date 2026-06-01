#pragma once

// Sunucu baglanti bilgileri
#define SERVER_HOST   "10.205.95.216"
#define SERVER_PORT   8080
#define SERVER_WS_URI "ws://" SERVER_HOST ":8080/ws"

// Ses tampon boyutlari
#define RESP_BUF_SIZE 112000 // 16000Hz * 2 byte * 3.5 saniye
