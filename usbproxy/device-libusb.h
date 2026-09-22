#include <libusb-1.0/libusb.h>

#include "misc.h"

#define USB_REQUEST_TIMEOUT 1000

#define MAX_ATTEMPTS 5

#define ISO_BATCH_SIZE_DEFAULT 8
#define ISO_BATCH_SIZE_MAX 32

struct iso_packet_result {
	uint8_t *data;
	int actual_length;
	int status; // libusb_transfer_status
};

struct iso_batch_result {
	uint8_t *buffer;
	int num_packets;
	struct iso_packet_result packets[ISO_BATCH_SIZE_MAX];
	int total_length;
	bool success;
};

extern libusb_device			**devs;
extern libusb_device_handle		*dev_handle;
extern libusb_context			*context;
extern libusb_hotplug_callback_handle	callback_handle;

extern struct libusb_device_descriptor		device_device_desc;
extern struct libusb_config_descriptor		**device_config_desc;

extern pthread_t hotplug_monitor_thread;

int connect_device(int vendorId, int productId);
void reset_device();
void set_configuration(int configuration);
void claim_interface(int interface);
void release_interface(int interface);
void set_interface_alt_setting(int interface, int altsetting);
int control_request(const usb_ctrlrequest *setup_packet, int *nbytes,
			unsigned char **dataptr, int timeout);
int send_data(uint8_t endpoint, uint8_t attributes, uint8_t *dataptr,
			int length, int timeout);
int send_iso_data(uint8_t endpoint, uint8_t *dataptr, int length, int timeout);
int receive_data(uint8_t endpoint, uint8_t attributes, uint16_t maxPacketSize,
			uint8_t **dataptr, int *length, int timeout);
int receive_iso_data_batched(uint8_t endpoint, uint16_t maxPacketSize,
			struct iso_batch_result *result, int batch_size, int timeout);

/*
 * Interrupt IN receive ring (2026-09-22).
 *
 * Reading an interrupt endpoint with a single in-flight libusb transfer
 * halves the effective report rate: measured on this board's full-speed
 * link, 1 transfer gives ~2.00 ms between reports (500 Hz) while 2+ give
 * ~1.00 ms (1000 Hz).  The host controller walks the periodic list once
 * per frame, so if the endpoint has no transfer queued at that instant the
 * frame is lost.  Keeping N transfers queued closes the gap between
 * completion and resubmission.
 *
 * One ring per endpoint (every endpoint owns a reader thread).  Completions
 * are served by the existing hotplug_monitor event thread.
 */
#define INT_RING_DEPTH		8	/* transfers kept in flight */
#define INT_RING_DEPTH_MAX	32
#define INT_RING_QUEUE_MAX	64	/* completed reports buffered before drop */

struct interrupt_ring;		/* opaque */

struct interrupt_ring *interrupt_ring_create(uint8_t endpoint, uint16_t maxPacketSize, int depth);
int interrupt_ring_next(struct interrupt_ring *ring, unsigned char **dataptr, int *length);
void interrupt_ring_destroy(struct interrupt_ring *ring);
