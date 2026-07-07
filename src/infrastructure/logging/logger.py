import logging
from datetime import datetime
from pathlib import Path


def configurar_logger(nombre_proceso, log_dir, level=logging.INFO):
    """Configura un logger basico con salida a consola y archivo."""
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_path / f"{nombre_proceso}_{timestamp}.log"

    logger = logging.getLogger(nombre_proceso)
    logger.setLevel(level)
    logger.handlers.clear()
    logger.propagate = False

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    stream_handler.setLevel(level)

    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    logger.info("Logger configurado. Archivo: %s", log_file)

    return logger

