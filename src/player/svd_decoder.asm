; TS2068 SVD v0 ECM decoder core (provisional, Pasmo-compatible syntax)
;
; This source is the readable decoder design. The checked-in Python contract
; validator prevents opcode drift; a deterministic byte emitter will become
; build authority before the first TAP/Fuse artifact.
;
; Decoder state while processing DELTA commands:
;   HL = stream source
;   IX = current bitmap cell address (Spectrum display-file order by row)
;   IY = current ECM attribute address, paired with IX at offset +$2000
;
; Commands advance both destination pointers, including plane-specific runs.
; The command loop does not depend on a transport: RAM, TAP-loaded blocks, and
; cartridge-window readers can arrange/replace the stream source separately.

                ORG     $8000

PLANE_SIZE      EQU     $1800
PIX_BASE        EQU     $4000
ATR_BASE        EQU     $6000

FRAME_KEY       EQU     1
FRAME_DELTA     EQU     2
FRAME_REPEAT    EQU     3
FRAME_SPARSE    EQU     4

CMD_END         EQU     0
CMD_SKIP        EQU     1
CMD_BITMAP      EQU     2
CMD_ATTRIBUTE   EQU     3
CMD_BOTH        EQU     4

; ---------------------------------------------------------------------------
; DECODE_KEY
; Entry: HL = 12288-byte payload (bitmap followed by attributes)
; Exit:  HL = first byte after payload
; Cost excluding contention: two LDIRs, approximately 258,060 T-states total.
; ---------------------------------------------------------------------------
DECODE_KEY:
                LD      DE,PIX_BASE
                LD      BC,PLANE_SIZE
                LDIR
                LD      DE,ATR_BASE
                LD      BC,PLANE_SIZE
                LDIR
                RET

; ---------------------------------------------------------------------------
; DECODE_SPARSE
; Entry: HL = bitmap count, then absolute-address/value records, followed by
;        attribute count and records. Each record is address low/high, value.
; Exit:  HL = first byte after payload. No row-address conversion is required.
; ---------------------------------------------------------------------------
DECODE_SPARSE:
                CALL    SPARSE_BLOCK
                CALL    SPARSE_BLOCK
                RET

SPARSE_BLOCK:   LD      C,(HL)
                INC     HL
                LD      B,(HL)
                INC     HL
                LD      A,B
                OR      C
                RET     Z
SPARSE_LOOP:    LD      E,(HL)
                INC     HL
                LD      D,(HL)
                INC     HL
                LD      A,(HL)
                INC     HL
                LD      (DE),A
                DEC     BC
                LD      A,B
                OR      C
                JR      NZ,SPARSE_LOOP
                RET

; ---------------------------------------------------------------------------
; DECODE_DELTA
; Entry: HL = first command
; Exit:  HL = byte after CMD_END, carry clear on success
; Error: carry set for unknown command or destination overrun
; ---------------------------------------------------------------------------
DECODE_DELTA:
                LD      IX,PIX_BASE
                LD      IY,ATR_BASE
                LD      DE,BITMAP_ROWS+2
                LD      (NEXT_ROW_PTR),DE

COMMAND_LOOP:
                LD      A,(HL)
                INC     HL
                PUSH    IY
                POP     DE
                LD      C,A                 ; preserve command during bound check
                LD      A,D
                CP      $78
                JR      C,COMMAND_IN_RANGE
                JP      NZ,DECODE_ERROR
                LD      A,E
                OR      A
                JP      NZ,DECODE_ERROR
                LD      A,C
                OR      A                   ; only END is legal at $7800
                JR      Z,END_FRAME
                JP      DECODE_ERROR
COMMAND_IN_RANGE:
                LD      A,C
                OR      A
                JR      Z,END_FRAME
                CP      CMD_SKIP
                JR      Z,DO_SKIP
                CP      CMD_BITMAP
                JR      Z,DO_BITMAP
                CP      CMD_ATTRIBUTE
                JR      Z,DO_ATTRIBUTE
                CP      CMD_BOTH
                JR      Z,DO_BOTH
                SCF
                RET

; u16 count. Encoder never emits zero.
DO_SKIP:
                LD      E,(HL)
                INC     HL
                LD      D,(HL)
                INC     HL
                LD      A,D
                OR      E
                JR      Z,DECODE_ERROR
                ADD     IX,DE
                ADD     IY,DE
                JR      CHECK_ROW

; u8 count; zero deliberately drives DJNZ through 256 iterations.
DO_BITMAP:
                LD      B,(HL)
                INC     HL
BITMAP_LOOP:
                LD      A,(HL)
                INC     HL
                LD      (IX+0),A
                INC     IX
                INC     IY
                DJNZ    BITMAP_LOOP
                JR      CHECK_ROW

DO_ATTRIBUTE:
                LD      B,(HL)
                INC     HL
ATTRIBUTE_LOOP:
                LD      A,(HL)
                INC     HL
                LD      (IY+0),A
                INC     IX
                INC     IY
                DJNZ    ATTRIBUTE_LOOP
                JR      CHECK_ROW

DO_BOTH:
                LD      B,(HL)
                INC     HL
BOTH_LOOP:
                LD      A,(HL)
                INC     HL
                LD      (IX+0),A
                LD      A,(HL)
                INC     HL
                LD      (IY+0),A
                INC     IX
                INC     IY
                DJNZ    BOTH_LOOP

; At each 32-cell boundary load the next scrambled bitmap row; the ECM
; attribute row has the identical low 13-bit offset at base $6000.
CHECK_ROW:
                PUSH    IY
                POP     DE
                LD      A,E
                AND     $1F
                JP      NZ,COMMAND_LOOP
                LD      A,D
                CP      $78
                JP      Z,COMMAND_LOOP
                PUSH    HL
                LD      HL,(NEXT_ROW_PTR)
                LD      E,(HL)
                INC     HL
                LD      D,(HL)
                INC     HL
                LD      (NEXT_ROW_PTR),HL
                PUSH    DE
                POP     IX
                SET     5,D                 ; $4000 bitmap row -> $6000 ECM row
                PUSH    DE
                POP     IY
                POP     HL
                JP      COMMAND_LOOP

DECODE_ERROR:
                SCF
                RET

END_FRAME:
                PUSH    IY
                POP     DE
                LD      A,D
                CP      $78
                JR      NZ,DECODE_ERROR
                LD      A,E
                OR      A
                JR      NZ,DECODE_ERROR
                OR      A                   ; clear carry
                RET

; A REPEAT frame has no payload and needs no decoder operation.

NEXT_ROW_PTR:   DW      0

BITMAP_ROWS:
                INCLUDE "bitmap_rows.inc"
